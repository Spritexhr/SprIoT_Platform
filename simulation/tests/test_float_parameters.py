import math
import os
import sys
import unittest
from unittest.mock import Mock


SIMULATION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SIMULATION_DIR not in sys.path:
    sys.path.insert(0, SIMULATION_DIR)

from common.mqtt_node import MqttNode
from common.registry import discover_registry
from common.schema import validate_entry
from common.waveforms import validate_waveform_config
from devices.sg90_servo.sg90_servo import SG90Servo
from sensors.generic_sensor.generic_sensor import GenericSensor
from sensors.temp_humi_sensor.temp_humi_sensor import TempHumiSensor


class FloatParameterTests(unittest.TestCase):
    def test_all_continuous_interval_schemas_and_commands_are_float(self):
        for module_name, node_cls in discover_registry().items():
            with self.subTest(module=module_name, surface="params"):
                for spec in node_cls.PARAMS_SCHEMA:
                    if spec.name in {"sampling_interval", "status_report_interval", "initial_angle"}:
                        self.assertEqual(spec.type, "float")

            with self.subTest(module=module_name, surface="commands"):
                for command in node_cls.SUPPORTED_COMMANDS:
                    for argument in command.get("args", []):
                        if argument["name"] in {"interval", "angle"}:
                            self.assertEqual(argument["type"], "float")

    def test_sensor_schema_accepts_decimal_intervals(self):
        errors, warnings = validate_entry(
            TempHumiSensor,
            {
                "module": "temp_humi_sensor",
                "id": "DHT-FLOAT-001",
                "sampling_interval": 10.25,
                "status_report_interval": 30.5,
            },
        )

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_servo_schema_accepts_decimal_angle(self):
        errors, warnings = validate_entry(
            SG90Servo,
            {
                "module": "sg90_servo",
                "id": "SERVO-FLOAT-001",
                "initial_angle": 90.5,
                "status_report_interval": 30.25,
            },
        )

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_runtime_commands_preserve_decimal_values(self):
        sensor = TempHumiSensor(
            node_id="DHT-FLOAT-002",
            broker="127.0.0.1",
            sampling_interval=10.25,
            status_report_interval=30.5,
        )
        sensor.publish_status = Mock(return_value=True)
        sensor.handle_command("set_interval", {"interval": "12.75"}, None)
        sensor.handle_command("set_status_interval", {"interval": 45.5}, None)

        servo = SG90Servo(
            node_id="SERVO-FLOAT-002",
            broker="127.0.0.1",
            initial_angle=90.5,
            status_report_interval=30.25,
        )
        servo.publish_status = Mock(return_value=True)
        servo.handle_command("set_angle", {"angle": "91.75"}, None)

        self.assertEqual(sensor.sampling_interval, 12.75)
        self.assertEqual(sensor.status_report_interval, 45.5)
        self.assertEqual(servo.current_angle, 91.75)

    def test_non_finite_numbers_are_rejected(self):
        for value in (True, float("nan"), float("inf"), "-inf", "not-a-number"):
            with self.subTest(value=value):
                self.assertIsNone(MqttNode.coerce_number(value))

        for value in (float("nan"), float("inf")):
            with self.subTest(value=value):
                errors = validate_waveform_config({"type": "constant", "value": value})
                self.assertTrue(errors)

    def test_discrete_precision_still_requires_an_integer(self):
        errors, _warnings = validate_entry(
            GenericSensor,
            {
                "module": "generic_sensor",
                "id": "GEN-FLOAT-003",
                "fields": {
                    "value": {
                        "waveform": {"type": "constant", "value": 20.5},
                        "precision": 1.5,
                    },
                },
            },
        )
        self.assertTrue(any("precision 应为 0-6 的整数" in error for error in errors))

        self.assertTrue(math.isfinite(MqttNode.coerce_number("1.25")))


if __name__ == "__main__":
    unittest.main()
