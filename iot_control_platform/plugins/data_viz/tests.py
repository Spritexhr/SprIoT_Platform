from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from devices.models import Device, DeviceStatusCollection, DeviceType
from devices.online_status import (
    clear_device_offline_timeout_cache,
    get_device_offline_timeout,
)
from sensors.models import Sensor, SensorData, SensorStatusCollection, SensorType


class DataVizApiTests(APITestCase):
    """data_viz 来源与时序接口的查询数和输入边界回归测试。"""

    def setUp(self):
        user = get_user_model().objects.create_user(
            username="data-viz-viewer",
            password="test",
        )
        self.client.force_authenticate(user)
        self.sensor_type = SensorType.objects.create(
            SensorType_id="data-viz-sensor",
            name="可视化传感器",
            data_fields=["value"],
            config_parameters=[],
            commands={},
        )
        self.device_type = DeviceType.objects.create(
            DeviceType_id="data-viz-device",
            name="可视化设备",
            config_parameters=["state"],
            commands={},
        )

        self.sensors = []
        self.devices = []
        for index in range(5):
            self.sensors.append(Sensor.objects.create(
                sensor_id=f"DATA-VIZ-SENSOR-{index}",
                name=f"可视化传感器 {index}",
                location=f"区域 {index}",
                sensor_type=self.sensor_type,
            ))
            self.devices.append(Device.objects.create(
                device_id=f"DATA-VIZ-DEVICE-{index}",
                name=f"可视化设备 {index}",
                location=f"区域 {index}",
                device_type=self.device_type,
            ))

        now = timezone.now()
        self.window_start = now - timedelta(hours=2)
        self.window_end = now + timedelta(minutes=1)
        timestamps = (
            now - timedelta(minutes=50),
            now - timedelta(minutes=20),
            now,
        )
        for index, timestamp in enumerate(timestamps, start=1):
            SensorData.objects.create(
                sensor=self.sensors[0],
                data={"value": index},
                timestamp=timestamp,
            )
            SensorStatusCollection.objects.create(
                sensor=self.sensors[0],
                data={"state": index},
                event_name=f"sensor-event-{index}",
                timestamp=timestamp,
            )
            DeviceStatusCollection.objects.create(
                device=self.devices[0],
                data={"state": index},
                event_name=f"device-event-{index}",
                timestamp=timestamp,
            )

    def series_params(self, kind="sensor", source_id=None, **overrides):
        if source_id is None:
            source_id = (
                self.sensors[0].sensor_id
                if kind == "sensor"
                else self.devices[0].device_id
            )
        params = {
            "kind": kind,
            "source_id": source_id,
            "start": self.window_start.isoformat(),
            "end": self.window_end.isoformat(),
            "limit": "2",
        }
        params.update(overrides)
        return params

    def test_sources_use_two_queries_for_multiple_resources(self):
        clear_device_offline_timeout_cache()
        get_device_offline_timeout()
        with self.assertNumQueries(2):
            response = self.client.get(reverse("data-viz-sources"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["sensors"]), 5)
        self.assertEqual(len(response.data["devices"]), 5)
        sensor = next(
            row for row in response.data["sensors"]
            if row["id"] == self.sensors[0].sensor_id
        )
        device = next(
            row for row in response.data["devices"]
            if row["id"] == self.devices[0].device_id
        )
        self.assertEqual(sensor["type"], self.sensor_type.name)
        self.assertEqual(sensor["data_fields"], ["value"])
        self.assertTrue(sensor["is_online"])
        self.assertEqual(device["type"], self.device_type.name)
        self.assertEqual(device["config_parameters"], ["state"])
        self.assertTrue(device["is_online"])

    def test_sensor_series_has_fixed_queries_and_preserves_response_shape(self):
        with self.assertNumQueries(4):
            response = self.client.get(
                reverse("data-viz-series"),
                self.series_params(),
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data),
            {
                "kind", "source_id", "name", "type", "fields", "start", "end",
                "points", "count", "truncated", "events",
            },
        )
        self.assertEqual(response.data["count"], 3)
        self.assertTrue(response.data["truncated"])
        self.assertEqual(
            [point["data"]["value"] for point in response.data["points"]],
            [2, 3],
        )
        self.assertEqual(len(response.data["events"]), 2)

    def test_device_series_has_fixed_queries_and_preserves_response_shape(self):
        with self.assertNumQueries(3):
            response = self.client.get(
                reverse("data-viz-series"),
                self.series_params(kind="device"),
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 3)
        self.assertTrue(response.data["truncated"])
        self.assertEqual(
            [point["data"]["state"] for point in response.data["points"]],
            [2, 3],
        )
        self.assertEqual(
            [event["event"] for event in response.data["events"]],
            ["device-event-2", "device-event-3"],
        )

    def test_invalid_datetime_and_limit_params_return_400(self):
        invalid_overrides = (
            {"limit": "0"},
            {"limit": "-1"},
            {"limit": "10001"},
            {"limit": "1.5"},
            {"limit": "many"},
            {"limit": ""},
            {"limit": "+2"},
            {"limit": "２"},
            {"start": ""},
            {"start": "not-a-date"},
            {"start": "2026-02-30T12:00:00"},
            {"end": ""},
            {"end": "not-a-date"},
            {"end": "2026-02-30T12:00:00"},
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                response = self.client.get(
                    reverse("data-viz-series"),
                    self.series_params(**overrides),
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("detail", response.data)

    def test_time_window_must_be_positive_and_at_most_31_days(self):
        end = timezone.now()
        invalid_windows = (
            {
                "start": end.isoformat(),
                "end": end.isoformat(),
            },
            {
                "start": (end + timedelta(seconds=1)).isoformat(),
                "end": end.isoformat(),
            },
            {
                "start": (end - timedelta(days=31, seconds=1)).isoformat(),
                "end": end.isoformat(),
            },
        )
        for window in invalid_windows:
            with self.subTest(window=window):
                response = self.client.get(
                    reverse("data-viz-series"),
                    self.series_params(**window),
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # 极小 end 在推导默认 24 小时 start 时会发生日期下溢，也必须稳定返回 400。
        response = self.client.get(
            reverse("data-viz-series"),
            {
                "kind": "sensor",
                "source_id": self.sensors[0].sensor_id,
                "end": "0001-01-01T00:00:00+00:00",
                "limit": "2",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.get(
            reverse("data-viz-series"),
            self.series_params(
                start=(end - timedelta(days=31)).isoformat(),
                end=end.isoformat(),
                limit="10000",
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_missing_source_keeps_404_response(self):
        response = self.client.get(
            reverse("data-viz-series"),
            self.series_params(source_id="MISSING-SENSOR"),
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("不存在", response.data["detail"])
