import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from services.base_command_send_service import BaseCommandSendService
from services.mqtt_command_bus import MqttCommandBus, MqttCommandWorker
from services.tests.test_mqtt_command_bus import FakeMqttService, FakeRedis


@override_settings(
    MQTT_BUS_PREFIX="test:delivery-semantics",
    MQTT_COMMAND_RESULT_TTL=60,
    MQTT_CHECK_CODE_TTL=30,
)
class MqttCommandDeliverySemanticsTests(SimpleTestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.web_bus = MqttCommandBus(
            self.redis,
            prefix="test:delivery-semantics",
        )
        self.runner_bus = MqttCommandBus(
            self.redis,
            prefix="test:delivery-semantics",
        )

    def _enqueue(
        self,
        *,
        require_device_ack=False,
        device_ack_timeout=0.2,
    ):
        request_id = self.web_bus.enqueue_command(
            resource_type="device",
            resource_id="VALVE-1",
            topic="iot/devices/VALVE-1/control",
            payload={"command": "set", "opening": 50},
            require_device_ack=require_device_ack,
            broker_ack_timeout=0.2,
            device_ack_timeout=device_ack_timeout,
        )
        message_id, fields = self.redis.streams[
            self.web_bus.command_stream
        ][0]
        return request_id, message_id, fields

    def test_puback_timeout_and_disconnect_race_are_delivery_unknown(self):
        for error in (
            "puback_timeout",
            "puback_wait_exception",
            "publish_call_exception",
            "publish_rc_4",
        ):
            with self.subTest(error=error):
                self.setUp()
                request_id, message_id, fields = self._enqueue()
                worker = MqttCommandWorker(
                    self.runner_bus,
                    FakeMqttService(result=(False, error)),
                    threading.Event(),
                )

                worker._process(message_id, fields, recovered=False)

                result = self.web_bus.get_request(request_id)
                self.assertEqual(result["status"], "delivery_unknown")
                self.assertEqual(result["error"], error)
                self.assertNotEqual(result["status"], "broker_timeout")

    def test_invalid_or_full_paho_queue_is_rejected_not_broker_timeout(self):
        for error in ("invalid_message", "publish_rc_15"):
            with self.subTest(error=error):
                self.setUp()
                request_id, message_id, fields = self._enqueue()
                worker = MqttCommandWorker(
                    self.runner_bus,
                    FakeMqttService(result=(False, error)),
                    threading.Event(),
                )

                worker._process(message_id, fields, recovered=False)

                self.assertEqual(
                    self.web_bus.get_request(request_id)["status"],
                    "rejected",
                )

    def test_uncertain_puback_still_waits_for_and_accepts_device_ack(self):
        request_id, message_id, fields = self._enqueue(
            require_device_ack=True,
            device_ack_timeout=1.0,
        )
        check_code = json.loads(fields["payload_json"])["check_code"]
        worker = MqttCommandWorker(
            self.runner_bus,
            FakeMqttService(result=(False, "puback_timeout")),
            threading.Event(),
        )
        thread = threading.Thread(
            target=worker._process,
            args=(message_id, fields),
            kwargs={"recovered": False},
        )
        thread.start()

        deadline = time.monotonic() + 1
        while (
            self.web_bus.get_request(request_id).get("status")
            != "awaiting_device_ack"
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)

        awaiting = self.web_bus.get_request(request_id)
        self.assertEqual(awaiting["status"], "awaiting_device_ack")
        self.assertEqual(awaiting["broker_delivery"], "unknown")
        self.assertEqual(awaiting["broker_error"], "puback_timeout")
        self.assertTrue(
            self.web_bus.resolve_check_code("device", "VALVE-1", check_code)
        )
        thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(
            self.web_bus.get_request(request_id)["status"],
            "device_acked",
        )
        self.assertEqual(self.redis.streams[self.web_bus.command_stream], [])

    def test_recovered_unknown_awaiting_state_keeps_unknown_timeout_semantics(self):
        request_id, message_id, fields = self._enqueue(
            require_device_ack=True,
            device_ack_timeout=0.1,
        )
        self.web_bus.transition_if_pending(
            request_id,
            "awaiting_device_ack",
            extra={
                "broker_delivery": "unknown",
                "broker_error": "puback_timeout",
                "device_ack_wait_started_at_ms": int(time.time() * 1000) - 500,
            },
        )
        worker = MqttCommandWorker(
            self.runner_bus,
            FakeMqttService(),
            threading.Event(),
        )

        worker._process(message_id, fields, recovered=True)

        result = self.web_bus.get_request(request_id)
        self.assertEqual(result["status"], "delivery_unknown")
        self.assertIn("puback_timeout", result["error"])

    def test_late_real_ack_upgrades_delivery_unknown(self):
        request_id, message_id, fields = self._enqueue(
            require_device_ack=True,
            device_ack_timeout=0.1,
        )
        check_code = json.loads(fields["payload_json"])["check_code"]
        worker = MqttCommandWorker(
            self.runner_bus,
            FakeMqttService(result=(False, "puback_timeout")),
            threading.Event(),
        )

        worker._process(message_id, fields, recovered=False)
        self.assertEqual(
            self.web_bus.get_request(request_id)["status"],
            "delivery_unknown",
        )

        self.assertTrue(
            self.web_bus.resolve_check_code("device", "VALVE-1", check_code)
        )
        self.assertEqual(
            self.web_bus.get_request(request_id)["status"],
            "device_acked",
        )
        self.assertIsNone(
            self.redis.get(self.web_bus._check_key(check_code))
        )

    def test_failed_device_ack_upgrade_keeps_check_code_mapping(self):
        request_id, _message_id, fields = self._enqueue(
            require_device_ack=True,
        )
        check_code = json.loads(fields["payload_json"])["check_code"]
        self.web_bus.set_state(
            request_id,
            "rejected",
            error="invalid command",
        )

        resolved = self.web_bus.resolve_check_code(
            "device",
            "VALVE-1",
            check_code,
        )

        self.assertFalse(resolved)
        self.assertIsNotNone(
            self.redis.get(self.web_bus._check_key(check_code))
        )
        self.assertEqual(
            self.web_bus.get_request(request_id)["status"],
            "rejected",
        )


class BaseCommandDeadlineBudgetTests(SimpleTestCase):
    @override_settings(MQTT_COMMAND_BROKER_ACK_TIMEOUT=2.0)
    def test_queue_start_deadline_and_caller_wait_budget_are_separate(self):
        service = BaseCommandSendService()
        service.id_field_name = "device_id"
        obj = SimpleNamespace(mqtt_topic_control="iot/devices/VALVE-1/control")

        cases = (
            {
                "require_device_ack": False,
                "device_timeout": 3.0,
                "terminal_status": "broker_acked",
                "execute_within": 3.0,
                "caller_wait": 6.0,
            },
            {
                "require_device_ack": True,
                "device_timeout": 3.0,
                "terminal_status": "device_acked",
                "execute_within": 6.0,
                "caller_wait": 12.0,
            },
        )

        for case in cases:
            with self.subTest(require_device_ack=case["require_device_ack"]):
                bus = Mock()
                bus.enqueue_command.return_value = "request-1"
                bus.wait_result.return_value = {
                    "status": case["terminal_status"],
                    "error": "",
                }
                with (
                    patch.object(service, "_get_object", return_value=obj),
                    patch(
                        "services.base_command_send_service.get_mqtt_command_bus",
                        return_value=bus,
                    ),
                ):
                    success = service._publish_command(
                        "VALVE-1",
                        {"command": "set"},
                        require_device_ack=case["require_device_ack"],
                        timeout=case["device_timeout"],
                    )

                self.assertTrue(success)
                enqueue_kwargs = bus.enqueue_command.call_args.kwargs
                self.assertEqual(
                    enqueue_kwargs["execute_within"],
                    case["execute_within"],
                )
                self.assertEqual(
                    bus.wait_result.call_args.kwargs["timeout"],
                    case["caller_wait"],
                )
                self.assertGreater(
                    bus.wait_result.call_args.kwargs["timeout"],
                    enqueue_kwargs["execute_within"],
                )
