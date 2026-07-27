"""MQTT 消息身份绑定的安全回归测试。"""

from unittest.mock import Mock, patch

from django.test import SimpleTestCase


class MqttTopicIdentitySecurityTests(SimpleTestCase):
    """载荷中的资源 ID 不得越过 broker topic ACL 写入其它资源。"""

    @patch(
        "services.sensors_service.sensor_upload_data_handlers._save_data",
        return_value=True,
    )
    @patch("services.sensors_service.sensor_upload_data_handlers._get_sensor")
    def test_sensor_data_topic_must_match_sensor_binding(self, get_sensor, save_data):
        from services.sensors_service.sensor_upload_data_handlers import (
            handle_mqtt_data_message,
        )

        get_sensor.return_value = Mock(
            mqtt_topic_data="iot/sensors/S-TARGET/data",
        )
        accepted = handle_mqtt_data_message(
            "iot/sensors/S-ATTACKER/data",
            {
                "sensor_id": "S-TARGET",
                "data": {"temperature": 99},
                "timestamp": 1,
            },
        )

        self.assertFalse(accepted)
        save_data.assert_not_called()

    @patch(
        "services.sensors_service.sensor_upload_status_handlers._save_status",
        return_value=True,
    )
    @patch("services.sensors_service.sensor_upload_status_handlers._get_sensor")
    def test_sensor_status_topic_must_match_sensor_binding(
        self,
        get_sensor,
        save_status,
    ):
        from services.sensors_service.sensor_upload_status_handlers import (
            handle_mqtt_status_message,
        )

        get_sensor.return_value = Mock(
            mqtt_topic_status="iot/sensors/S-TARGET/status",
        )
        accepted = handle_mqtt_status_message(
            "iot/sensors/S-ATTACKER/status",
            {
                "sensor_id": "S-TARGET",
                "status": {"online": True},
                "event": "online",
                "timestamp": 1,
            },
        )

        self.assertFalse(accepted)
        save_status.assert_not_called()

    @patch(
        "services.devices_service.device_upload_status_handlers._save_device_status",
        return_value=True,
    )
    @patch("services.devices_service.device_upload_status_handlers._get_device")
    def test_device_status_topic_must_match_device_binding(
        self,
        get_device,
        save_status,
    ):
        from services.devices_service.device_upload_status_handlers import (
            handle_mqtt_device_status_message,
        )

        get_device.return_value = Mock(
            mqtt_topic_data="iot/devices/D-TARGET/status",
        )
        accepted = handle_mqtt_device_status_message(
            "iot/devices/D-ATTACKER/status",
            {
                "device_id": "D-TARGET",
                "status": {"power": True},
                "event": "state",
                "timestamp": 1,
            },
        )

        self.assertFalse(accepted)
        save_status.assert_not_called()
