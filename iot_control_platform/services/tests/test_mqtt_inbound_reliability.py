"""MQTT 入站消息可靠性回归测试。"""

import threading
import time
from unittest.mock import Mock, patch

from django.db import InterfaceError, OperationalError
from django.db.models.signals import post_save
from django.test import SimpleTestCase, TestCase

from devices.models import Device, DeviceStatusCollection, DeviceType
from sensors.models import Sensor, SensorData, SensorStatusCollection, SensorType
from services.devices_service.device_upload_status_handlers import (
    handle_mqtt_device_status_message,
)
from services.sensors_service.sensor_upload_data_handlers import (
    handle_mqtt_data_message,
)
from services.sensors_service.sensor_upload_status_handlers import (
    handle_mqtt_status_message,
)


class MqttInboundIdempotencyTests(TestCase):
    def setUp(self):
        sensor_type = SensorType.objects.create(
            SensorType_id="reliability-sensor-type",
            name="可靠性测试传感器",
            data_fields=["value"],
            config_parameters=[],
            commands={},
        )
        self.sensor = Sensor.objects.create(
            sensor_id="S-RELIABLE",
            name="可靠性测试传感器",
            sensor_type=sensor_type,
        )
        device_type = DeviceType.objects.create(
            DeviceType_id="reliability-device-type",
            name="可靠性测试设备",
            config_parameters=["power"],
            commands={},
        )
        self.device = Device.objects.create(
            device_id="D-RELIABLE",
            name="可靠性测试设备",
            device_type=device_type,
        )

    def test_sensor_data_message_id_prevents_qos_redelivery_duplicates(self):
        payload = {
            "sensor_id": self.sensor.sensor_id,
            "data": {"value": 1},
            "timestamp": 1,
            "message_id": "sensor-data-001",
        }

        self.assertTrue(
            handle_mqtt_data_message(self.sensor.mqtt_topic_data, payload)
        )
        self.assertTrue(
            handle_mqtt_data_message(self.sensor.mqtt_topic_data, payload)
        )

        records = SensorData.objects.filter(sensor=self.sensor)
        self.assertEqual(records.count(), 1)
        self.assertEqual(records.get().message_id, "sensor-data-001")

    def test_reused_message_id_with_different_payload_is_rejected(self):
        original = {
            "sensor_id": self.sensor.sensor_id,
            "data": {"value": 1},
            "timestamp": 1,
            "message_id": "sensor-data-collision-001",
        }
        collision = {
            **original,
            "data": {"value": 2},
        }

        self.assertTrue(
            handle_mqtt_data_message(self.sensor.mqtt_topic_data, original)
        )
        self.assertFalse(
            handle_mqtt_data_message(self.sensor.mqtt_topic_data, collision)
        )

        records = SensorData.objects.filter(sensor=self.sensor)
        self.assertEqual(records.count(), 1)
        self.assertEqual(records.get().data, {"value": 1})

    def test_message_uuid_alias_is_normalized_for_sensor_status(self):
        payload = {
            "sensor_id": self.sensor.sensor_id,
            "status": {"online": True},
            "event": "online",
            "timestamp": 1,
            "message_uuid": "sensor-status-001",
        }

        self.assertTrue(
            handle_mqtt_status_message(self.sensor.mqtt_topic_status, payload)
        )
        self.assertTrue(
            handle_mqtt_status_message(self.sensor.mqtt_topic_status, payload)
        )

        records = SensorStatusCollection.objects.filter(sensor=self.sensor)
        self.assertEqual(records.count(), 1)
        self.assertEqual(records.get().message_id, "sensor-status-001")

    def test_duplicate_status_skips_second_signal_but_retries_command_ack(self):
        payload = {
            "sensor_id": self.sensor.sensor_id,
            "status": {"online": True},
            "event": "online",
            "timestamp": 1,
            "message_id": "sensor-status-with-ack-001",
            "check_code": "123456",
        }
        saves = []

        def record_save(sender, instance, created, **kwargs):
            saves.append((instance.pk, created))

        post_save.connect(
            record_save,
            sender=SensorStatusCollection,
            weak=False,
            dispatch_uid="test_mqtt_duplicate_sensor_status",
        )
        try:
            with patch(
                "services.sensors_service."
                "sensor_upload_status_handlers._resolve_command_ack"
            ) as resolve_ack, patch(
                "services.realtime.signals.dispatch.publish_sensor_status"
            ) as publish_status:
                for _ in range(2):
                    with self.captureOnCommitCallbacks(execute=True):
                        self.assertTrue(
                            handle_mqtt_status_message(
                                self.sensor.mqtt_topic_status,
                                payload,
                            )
                        )
        finally:
            post_save.disconnect(
                sender=SensorStatusCollection,
                dispatch_uid="test_mqtt_duplicate_sensor_status",
            )

        # get_or_create 命中旧记录时不再 save，因此不会重复模型信号/实时广播。
        self.assertEqual(len(saves), 1)
        publish_status.assert_called_once()
        # 命令 ACK 刻意重试：首次提交后若 Redis 短暂失败，QoS 重投仍可补齐确认。
        self.assertEqual(resolve_ack.call_count, 2)

    def test_device_status_message_id_prevents_qos_redelivery_duplicates(self):
        payload = {
            "device_id": self.device.device_id,
            "status": {"power": True},
            "event": "state",
            "timestamp": 1,
            "message_id": "device-status-001",
        }

        self.assertTrue(
            handle_mqtt_device_status_message(
                self.device.mqtt_topic_data,
                payload,
            )
        )
        self.assertTrue(
            handle_mqtt_device_status_message(
                self.device.mqtt_topic_data,
                payload,
            )
        )

        records = DeviceStatusCollection.objects.filter(device=self.device)
        self.assertEqual(records.count(), 1)
        self.assertEqual(records.get().message_id, "device-status-001")

    def test_legacy_messages_without_id_remain_backward_compatible(self):
        payload = {
            "sensor_id": self.sensor.sensor_id,
            "data": {"value": 1},
            "timestamp": 1,
        }

        self.assertTrue(
            handle_mqtt_data_message(self.sensor.mqtt_topic_data, payload)
        )
        self.assertTrue(
            handle_mqtt_data_message(self.sensor.mqtt_topic_data, payload)
        )

        self.assertEqual(
            SensorData.objects.filter(sensor=self.sensor).count(),
            2,
        )

    def test_conflicting_message_id_aliases_are_rejected(self):
        payload = {
            "sensor_id": self.sensor.sensor_id,
            "data": {"value": 1},
            "timestamp": 1,
            "message_id": "message-a",
            "message_uuid": "message-b",
        }

        self.assertFalse(
            handle_mqtt_data_message(self.sensor.mqtt_topic_data, payload)
        )
        self.assertFalse(
            SensorData.objects.filter(sensor=self.sensor).exists()
        )

    def test_uppercase_message_id_is_rejected_before_mysql_collation_collision(self):
        payload = {
            "sensor_id": self.sensor.sensor_id,
            "data": {"value": 1},
            "timestamp": 1,
            "message_id": "Case-Sensitive-ID",
        }

        self.assertFalse(
            handle_mqtt_data_message(self.sensor.mqtt_topic_data, payload)
        )
        self.assertFalse(
            SensorData.objects.filter(sensor=self.sensor).exists()
        )


class MqttTemporaryDatabaseFailureTests(SimpleTestCase):
    @staticmethod
    def _message():
        message = Mock()
        message.topic = "iot/sensors/S-1/data"
        message.payload = (
            b'{"sensor_id":"S-1","data":{"value":1},"timestamp":1}'
        )
        message.qos = 1
        message.mid = 42
        return message

    def test_handlers_propagate_temporary_database_failures(self):
        payload = {
            "sensor_id": "S-1",
            "data": {"value": 1},
            "timestamp": 1,
        }
        for exception_type in (OperationalError, InterfaceError):
            with self.subTest(exception_type=exception_type.__name__):
                with patch(
                    "services.sensors_service."
                    "sensor_upload_data_handlers._get_sensor",
                    side_effect=exception_type("database unavailable"),
                ):
                    with self.assertRaises(exception_type):
                        handle_mqtt_data_message(
                            "iot/sensors/S-1/data",
                            payload,
                        )

    def test_temporary_database_failure_is_neither_acked_nor_dead_lettered(self):
        from services.mqtt_service import MQTTService

        service = MQTTService()
        handler = Mock(side_effect=OperationalError("database unavailable"))
        handler.__name__ = "temporary_database_failure"
        service.register_handler("iot/sensors/+/data", handler)
        client = Mock()
        bus = Mock()

        with patch.object(
            service,
            "_schedule_retryable_reconnect",
        ) as schedule_reconnect, patch(
            "services.mqtt_command_bus.get_mqtt_command_bus",
            return_value=bus,
        ):
            service._on_message(client, None, self._message())

        client.ack.assert_not_called()
        bus.dead_letter_inbound.assert_not_called()
        schedule_reconnect.assert_called_once_with(client)

    def test_retryable_reconnect_is_coalesced_and_runs_outside_callback(self):
        from services.mqtt_service import MQTTService

        service = MQTTService()
        client = Mock()
        service.client = client
        service._inbound_retry_delay = 0.01
        release_reconnect = threading.Event()
        reconnect_started = threading.Event()

        def reconnect():
            reconnect_started.set()
            release_reconnect.wait(timeout=1)
            return True

        with patch.object(service, "reconnect_async", side_effect=reconnect):
            service._schedule_retryable_reconnect(client)
            service._schedule_retryable_reconnect(client)
            self.assertTrue(reconnect_started.wait(timeout=1))
            self.assertTrue(service._retry_reconnect_pending)
            release_reconnect.set()

        deadline = time.monotonic() + 1
        while service._retry_reconnect_pending and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(service._retry_reconnect_pending)

    def test_retryable_reconnect_uses_independent_exponential_backoff(self):
        from services.mqtt_service import MQTTService

        service = MQTTService()
        client = Mock()
        service.client = client
        observed_delays = []

        class InlineThread:
            def __init__(self, *, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        with patch(
            "services.mqtt_service.threading.Thread",
            InlineThread,
        ), patch(
            "services.mqtt_service.time.sleep",
            side_effect=observed_delays.append,
        ), patch.object(
            service,
            "reconnect_async",
            return_value=True,
        ):
            service._schedule_retryable_reconnect(client)
            service._schedule_retryable_reconnect(client)
            service._schedule_retryable_reconnect(client)

        self.assertEqual(observed_delays, [1, 2, 4])
        self.assertEqual(service._inbound_retry_delay, 8)

    def test_successful_inbound_processing_resets_database_retry_backoff(self):
        from services.mqtt_service import MQTTService

        service = MQTTService()
        service._inbound_retry_delay = 16
        handler = Mock(return_value=True)
        handler.__name__ = "successful_handler"
        service.register_handler("iot/sensors/+/data", handler)
        client = Mock()
        client.ack.return_value = 0

        service._on_message(client, None, self._message())

        self.assertEqual(
            service._inbound_retry_delay,
            service.INBOUND_RETRY_MIN_DELAY,
        )
