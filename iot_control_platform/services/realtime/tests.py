from datetime import timedelta
from unittest.mock import patch

from channels.layers import InMemoryChannelLayer
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from devices.models import Device, DeviceStatusCollection, DeviceType
from sensors.models import Sensor, SensorData, SensorStatusCollection, SensorType
from projects.consumers import ProjectStreamConsumer

from .consumers import DeviceStreamConsumer, SensorStreamConsumer
from .dispatch import (
    CHANNEL_GROUP_MAX_LENGTH,
    g_device_one,
    g_plugin,
    g_project,
    g_sensor_one,
    make_resource_group,
)
from .latest_values import LatestValuesCache, PointSample


class ChannelGroupNameTests(SimpleTestCase):
    def test_safe_existing_identifiers_keep_their_group_names(self):
        self.assertEqual(g_sensor_one("sensor-1"), "sensors.sensor-1")
        self.assertEqual(g_device_one("device_1"), "devices.device_1")
        self.assertEqual(g_plugin("data_viz"), "plugins.data_viz")
        self.assertEqual(g_project(42), "projects.42")

    def test_unsafe_and_long_identifiers_are_legal_and_deterministic(self):
        groups = [
            g_sensor_one("车间:温度/一号"),
            g_device_one("执行器:一号"),
            g_plugin("中文:插件"),
            g_project("厂区:一"),
            g_sensor_one("x" * 300),
            g_sensor_one(""),
        ]
        layer = InMemoryChannelLayer()

        for group in groups:
            self.assertLess(len(group), CHANNEL_GROUP_MAX_LENGTH)
            self.assertTrue(group.isascii())
            layer.require_valid_group_name(group)

        self.assertEqual(g_sensor_one("车间:温度/一号"), groups[0])
        self.assertEqual(g_sensor_one("x" * 300), groups[4])

    def test_hash_suffix_prevents_sanitization_collisions(self):
        self.assertNotEqual(g_sensor_one("sensor:1"), g_sensor_one("sensor-1"))
        self.assertNotEqual(g_plugin("a/b"), g_plugin("a-b"))

    def test_invalid_namespace_is_rejected(self):
        with self.assertRaises(ValueError):
            make_resource_group("bad:namespace", "id")

    def test_consumers_and_publishers_share_the_same_sanitizer(self):
        sensor = SensorStreamConsumer()
        sensor.scope = {
            "url_route": {"kwargs": {"sensor_id": "车间:温度/一号"}},
        }
        device = DeviceStreamConsumer()
        device.scope = {
            "url_route": {"kwargs": {"device_id": "执行器:一号"}},
        }
        project = ProjectStreamConsumer()
        project.scope = {
            "url_route": {"kwargs": {"project_id": "厂区:一"}},
        }

        self.assertEqual(sensor._compute_groups(), [g_sensor_one("车间:温度/一号")])
        self.assertEqual(device._compute_groups(), [g_device_one("执行器:一号")])
        self.assertEqual(
            project._compute_groups(),
            [g_project("厂区:一"), "devices.all"],
        )


class LatestValuesCacheTests(SimpleTestCase):
    def test_same_sensor_id_is_kept_independently_per_plugin(self):
        cache = LatestValuesCache()
        first = PointSample(
            sensor_id="shared-sensor",
            plugin_code="plugin_a",
            tag="A",
            value=1.0,
        )
        second = PointSample(
            sensor_id="shared-sensor",
            plugin_code="plugin_b",
            tag="B",
            value=2.0,
        )

        cache.update(first)
        cache.update(second)

        self.assertIs(cache.get("plugin_a", "shared-sensor"), first)
        self.assertIs(cache.get("plugin_b", "shared-sensor"), second)
        self.assertEqual(cache.snapshot("plugin_a"), [first])
        self.assertEqual(cache.snapshot("plugin_b"), [second])
        self.assertCountEqual(cache.snapshot(), [first, second])


class AtomicIngestionTests(TestCase):
    """记录、在线状态和提交后广播必须属于同一个一致性边界。"""

    def setUp(self):
        self.sensor_type = SensorType.objects.create(
            SensorType_id="atomic-sensor-type",
            name="原子接入传感器",
            data_fields=["value"],
        )
        self.sensor = Sensor.objects.create(
            sensor_id="atomic-sensor",
            name="原子接入传感器",
            sensor_type=self.sensor_type,
        )
        self.device_type = DeviceType.objects.create(
            DeviceType_id="atomic-device-type",
            name="原子接入设备",
        )
        self.device = Device.objects.create(
            device_id="atomic-device",
            name="原子接入设备",
            device_type=self.device_type,
        )

    def test_sensor_future_device_timestamp_does_not_poison_last_seen(self):
        before = timezone.now()

        SensorData.objects.create(
            sensor=self.sensor,
            data={"value": 1},
            timestamp=before + timedelta(days=365),
        )

        after = timezone.now()
        self.sensor.refresh_from_db()
        self.assertTrue(self.sensor.is_online)
        self.assertGreaterEqual(self.sensor.last_seen, before)
        self.assertLessEqual(self.sensor.last_seen, after)

    def test_device_future_device_timestamp_does_not_poison_last_seen(self):
        before = timezone.now()

        DeviceStatusCollection.objects.create(
            device=self.device,
            data={"power": True},
            event_name="status",
            timestamp=before + timedelta(days=365),
        )

        after = timezone.now()
        self.device.refresh_from_db()
        self.assertTrue(self.device.is_online)
        self.assertGreaterEqual(self.device.last_seen, before)
        self.assertLessEqual(self.device.last_seen, after)

    def test_failed_record_insert_rolls_back_parent_heartbeat(self):
        with self.assertRaises(IntegrityError):
            SensorData.objects.create(
                sensor=self.sensor,
                data=None,
                timestamp=timezone.now(),
            )

        self.sensor.refresh_from_db()
        self.assertFalse(self.sensor.is_online)
        self.assertIsNone(self.sensor.last_seen)

    def test_sensor_status_broadcast_contains_updated_online_state(self):
        with (
            patch("services.realtime.signals.dispatch.publish_sensor_status") as publish,
            self.captureOnCommitCallbacks(execute=True),
        ):
            SensorStatusCollection.objects.create(
                sensor=self.sensor,
                data={"enabled": True},
                event_name="online",
                timestamp=timezone.now() - timedelta(days=1),
            )

        payload = publish.call_args.args[1]
        self.assertTrue(payload["is_online"])
        self.assertIsNotNone(payload["last_seen"])

    def test_device_status_broadcast_contains_updated_online_state(self):
        with (
            patch("services.realtime.signals.dispatch.publish_device_status") as publish,
            self.captureOnCommitCallbacks(execute=True),
        ):
            DeviceStatusCollection.objects.create(
                device=self.device,
                data={"power": True},
                event_name="online",
                timestamp=timezone.now() - timedelta(days=1),
            )

        payload = publish.call_args.args[1]
        self.assertTrue(payload["is_online"])
        self.assertIsNotNone(payload["last_seen"])


class DeviceTopicLifecycleTests(TestCase):
    def setUp(self):
        self.device_type = DeviceType.objects.create(
            DeviceType_id="topic-device-type",
            name="主题设备",
        )

    def test_renaming_device_regenerates_system_topics(self):
        device = Device.objects.create(
            device_id="device-old",
            name="主题设备",
            device_type=self.device_type,
        )

        device.device_id = "device-new"
        device.save(update_fields=["device_id"])
        device.refresh_from_db()

        self.assertEqual(device.mqtt_topic_data, "iot/devices/device-new/status")
        self.assertEqual(device.mqtt_topic_control, "iot/devices/device-new/control")

    def test_renaming_device_preserves_custom_topics(self):
        device = Device.objects.create(
            device_id="custom-old",
            name="自定义主题设备",
            device_type=self.device_type,
            mqtt_topic_data="factory/custom/status",
            mqtt_topic_control="factory/custom/control",
        )

        device.device_id = "custom-new"
        device.save(update_fields=["device_id"])
        device.refresh_from_db()

        self.assertEqual(device.mqtt_topic_data, "factory/custom/status")
        self.assertEqual(device.mqtt_topic_control, "factory/custom/control")
