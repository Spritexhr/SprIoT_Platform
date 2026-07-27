from datetime import timedelta
from unittest.mock import MagicMock, Mock, patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from automation.models import AutomationRule
from devices.models import Device, DeviceStatusCollection, DeviceType
from devices.online_status import clear_device_offline_timeout_cache
from sensors.models import Sensor, SensorData, SensorType


class DashboardStatsQueryPerformanceTests(APITestCase):
    """仪表盘查询数不能随最近资源数量线性增长。"""

    def setUp(self):
        user = get_user_model().objects.create_user(
            username="dashboard-query-viewer",
            password="test",
        )
        self.client.force_authenticate(user)
        sensor_type = SensorType.objects.create(
            SensorType_id="dashboard-query-sensor",
            name="仪表盘查询传感器",
            data_fields=["value"],
            config_parameters=[],
            commands={},
        )
        device_type = DeviceType.objects.create(
            DeviceType_id="dashboard-query-device",
            name="仪表盘查询设备",
            config_parameters=["state"],
            commands={},
        )
        now = timezone.now()
        self.sensors = []
        self.devices = []
        for index in range(5):
            sensor = Sensor.objects.create(
                sensor_id=f"DASHBOARD-SENSOR-{index}",
                name=f"仪表盘传感器 {index}",
                sensor_type=sensor_type,
            )
            device = Device.objects.create(
                device_id=f"DASHBOARD-DEVICE-{index}",
                name=f"仪表盘设备 {index}",
                device_type=device_type,
            )
            SensorData.objects.create(
                sensor=sensor,
                data={"value": f"old-{index}"},
                timestamp=now - timedelta(minutes=5),
            )
            SensorData.objects.create(
                sensor=sensor,
                data={"value": f"future-poison-{index}"},
                timestamp=now + timedelta(days=3650),
            )
            SensorData.objects.create(
                sensor=sensor,
                data={"value": f"latest-{index}"},
                timestamp=now,
            )
            DeviceStatusCollection.objects.create(
                device=device,
                data={"state": f"old-{index}"},
                event_name="old",
                timestamp=now - timedelta(minutes=5),
            )
            DeviceStatusCollection.objects.create(
                device=device,
                data={"state": f"future-poison-{index}"},
                event_name="future-poison",
                timestamp=now + timedelta(days=3650),
            )
            DeviceStatusCollection.objects.create(
                device=device,
                data={"state": f"latest-{index}"},
                event_name="latest",
                timestamp=now,
            )
            self.sensors.append(sensor)
            self.devices.append(device)

        self.empty_sensor = Sensor.objects.create(
            sensor_id="DASHBOARD-SENSOR-EMPTY",
            name="无数据传感器",
            sensor_type=sensor_type,
        )
        self.empty_device = Device.objects.create(
            device_id="DASHBOARD-DEVICE-EMPTY",
            name="无状态设备",
            device_type=device_type,
        )
        Sensor.objects.filter(pk=self.empty_sensor.pk).update(
            last_seen=now + timedelta(days=365),
            is_online=True,
        )
        Device.objects.filter(pk=self.empty_device.pk).update(
            last_seen=now + timedelta(days=365),
            is_online=True,
        )
        for index in range(3):
            AutomationRule.objects.create(
                name=f"仪表盘规则 {index}",
                script_id=f"dashboard_rule_{index}",
                script="def loop(): return True",
            )

    def test_dashboard_uses_fixed_queries_and_keeps_latest_values(self):
        # 包含一次统一 device_offline_timeout 配置读取；查询数仍与资源量无关。
        clear_device_offline_timeout_cache()
        with self.assertNumQueries(9):
            response = self.client.get(reverse("dashboard-stats"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["sensor_total"], 6)
        self.assertEqual(response.data["sensor_online"], 5)
        self.assertEqual(response.data["device_total"], 6)
        self.assertEqual(response.data["device_online"], 5)
        self.assertEqual(response.data["rule_total"], 3)
        self.assertEqual(response.data["sensor_data_24h"], 15)
        self.assertEqual(response.data["device_data_24h"], 15)

        sensor_rows = {
            row["sensor_id"]: row for row in response.data["recent_sensors"]
        }
        device_rows = {
            row["device_id"]: row for row in response.data["recent_devices"]
        }
        self.assertEqual(
            sensor_rows[self.sensors[0].sensor_id]["latest_data"],
            {"value": "latest-0"},
        )
        self.assertIsNone(sensor_rows[self.empty_sensor.sensor_id]["latest_data"])
        self.assertIs(sensor_rows[self.empty_sensor.sensor_id]["is_online"], False)
        self.assertEqual(
            device_rows[self.devices[0].device_id]["latest_data"],
            {"state": "latest-0"},
        )
        self.assertIsNone(device_rows[self.empty_device.device_id]["latest_data"])
        self.assertIs(device_rows[self.empty_device.device_id]["is_online"], False)


class HealthCheckSecurityTests(APITestCase):
    def test_public_health_check_does_not_expose_internal_exception_details(self):
        with patch(
            "config.api_views._check_database_connection",
            side_effect=RuntimeError(
                "mysql://secret-user:secret-pass@internal-db"
            ),
        ), patch(
            "services.mqtt_command_bus.get_mqtt_command_bus",
            side_effect=RuntimeError("redis://internal-redis:6379"),
        ):
            response = self.client.get(reverse("health-check"))

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["checks"], {
            "database": "error",
            "mqtt": "error",
        })
        self.assertNotIn("secret-pass", str(response.data))
        self.assertNotIn("internal-redis", str(response.data))

    def test_health_runs_real_query_even_when_connection_object_already_exists(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        bus = Mock()
        bus.get_runner_status.return_value = {
            "is_connected": "1",
            "command_worker_alive": "1",
        }

        if connection.vendor == "mysql":
            probe = MagicMock()
            probe.cursor.return_value = cursor
            database_probe = patch(
                "config.api_views.connection.Database.connect",
                return_value=probe,
            )
        else:
            database_probe = patch(
                "config.api_views.connection.cursor",
                return_value=cursor_context,
            )

        with database_probe, patch(
            "services.mqtt_command_bus.get_mqtt_command_bus",
            return_value=bus,
        ):
            response = self.client.get(reverse("health-check"))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        cursor.execute.assert_called_once_with("SELECT 1")
        if connection.vendor == "mysql":
            cursor.close.assert_called_once_with()
            probe.close.assert_called_once_with()
