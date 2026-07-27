import base64
import json
import threading
import time
from collections import defaultdict
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from services.mqtt_command_bus import (
    MAX_DEVICE_ACK_TIMEOUT_SECONDS,
    MqttCommandBus,
    MqttCommandBusUnavailable,
    MqttCommandQueueFull,
    MqttCommandWorker,
)


class _FakePipeline:
    def __init__(self, client):
        self.client = client

    def __getattr__(self, name):
        return getattr(self.client, name)

    def execute(self):
        return []

    def watch(self, *args):
        return True

    def unwatch(self):
        return True

    def multi(self):
        return True

    def reset(self):
        return True


class FakeRedis:
    """Small thread-safe redis-py test double for the bus protocol."""

    def __init__(self):
        self.hashes = defaultdict(dict)
        self.values = {}
        self.lists = defaultdict(list)
        self.streams = defaultdict(list)
        self.groups = set()
        self.pending = defaultdict(dict)
        self.xadd_options = []
        self._next_id = 1
        self._condition = threading.Condition()

    def pipeline(self, transaction=True):
        return _FakePipeline(self)

    def hset(self, key, mapping):
        with self._condition:
            self.hashes[key].update({str(k): str(v) for k, v in mapping.items()})
            return len(mapping)

    def hget(self, key, field):
        return self.hashes[key].get(field)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def expire(self, key, ttl):
        return True

    def set(self, key, value, nx=False, ex=None):
        with self._condition:
            if nx and key in self.values:
                return False
            self.values[key] = value
            return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)
        self.hashes.pop(key, None)
        self.lists.pop(key, None)
        return 1

    def rpush(self, key, value):
        with self._condition:
            self.lists[key].append(value)
            self._condition.notify_all()
            return len(self.lists[key])

    def blpop(self, key, timeout=0):
        deadline = time.monotonic() + float(timeout)
        with self._condition:
            while not self.lists[key]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return key, self.lists[key].pop(0)

    def xadd(self, stream, fields, **kwargs):
        self.xadd_options.append((stream, dict(kwargs)))
        message_id = f"{self._next_id}-0"
        self._next_id += 1
        self.streams[stream].append((message_id, dict(fields)))
        return message_id

    def xlen(self, stream):
        return len(self.streams[stream])

    def xgroup_create(self, stream, group, id="0-0", mkstream=False):
        marker = (stream, group)
        if marker in self.groups:
            import redis
            raise redis.ResponseError("BUSYGROUP Consumer Group name already exists")
        self.groups.add(marker)
        return True

    def xreadgroup(self, group, consumer, streams, count=1, block=0):
        stream = next(iter(streams))
        delivered_ids = set(self.pending[stream])
        entries = []
        for message_id, fields in self.streams[stream]:
            if message_id in delivered_ids:
                continue
            self.pending[stream][message_id] = (consumer, dict(fields))
            entries.append((message_id, dict(fields)))
            if len(entries) >= count:
                break
        return [(stream, entries)] if entries else []

    def xautoclaim(self, stream, group, consumer, min_idle, start_id, count=None):
        claimed = []
        for message_id, (_old_consumer, fields) in list(self.pending[stream].items()):
            self.pending[stream][message_id] = (consumer, fields)
            claimed.append((message_id, dict(fields)))
            if count and len(claimed) >= count:
                break
        return "0-0", claimed, []

    def xack(self, stream, group, message_id):
        self.pending[stream].pop(message_id, None)
        return 1

    def xdel(self, stream, message_id):
        self.streams[stream] = [
            item for item in self.streams[stream] if item[0] != message_id
        ]
        return 1


class FakeMqttService:
    def __init__(self, result=(True, "")):
        self.result = result
        self.calls = []

    def wait_until_connected(self, timeout=1):
        return True

    def publish_wait(self, topic, payload, *, qos, timeout):
        self.calls.append((topic, payload, qos, timeout))
        return self.result


@override_settings(
    MQTT_BUS_PREFIX="test:mqtt",
    MQTT_COMMAND_RESULT_TTL=60,
    MQTT_CHECK_CODE_TTL=30,
)
class MqttCommandBusTests(SimpleTestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.web_bus = MqttCommandBus(self.redis, prefix="test:mqtt")
        # Separate object models the mqtt_runner process; only Redis is shared.
        self.runner_bus = MqttCommandBus(self.redis, prefix="test:mqtt")

    def _one_command(self):
        message_id, fields = self.redis.streams[self.web_bus.command_stream][0]
        return message_id, fields

    def test_device_ack_crosses_process_boundary_without_in_memory_event(self):
        request_id = self.web_bus.enqueue_command(
            resource_type="device",
            resource_id="PUMP-1",
            topic="iot/devices/PUMP-1/control",
            payload={"command": "start"},
            require_device_ack=True,
        )
        _message_id, fields = self._one_command()
        check_code = json.loads(fields["payload_json"])["check_code"]

        result_holder = {}

        def wait_in_web_process():
            result_holder["value"] = self.web_bus.wait_result(request_id, 1)

        waiter = threading.Thread(target=wait_in_web_process)
        waiter.start()
        self.assertTrue(
            self.runner_bus.resolve_check_code("device", "PUMP-1", check_code)
        )
        waiter.join(1)

        self.assertEqual(result_holder["value"]["status"], "device_acked")

    def test_ordinary_command_completes_only_after_broker_ack(self):
        request_id = self.web_bus.enqueue_command(
            resource_type="sensor",
            resource_id="S-1",
            topic="iot/sensors/S-1/control",
            payload={"command": "calibrate"},
        )
        message_id, fields = self._one_command()
        mqtt = FakeMqttService()
        worker = MqttCommandWorker(self.runner_bus, mqtt, threading.Event())

        worker._process(message_id, fields, recovered=False)

        self.assertEqual(self.web_bus.get_request(request_id)["status"], "broker_acked")
        self.assertEqual(len(mqtt.calls), 1)

    @override_settings(MQTT_COMMAND_STREAM_MAX_BACKLOG=1)
    def test_command_backlog_limit_rejects_unbounded_growth(self):
        bus = MqttCommandBus(self.redis, prefix="test:bounded")
        bus.enqueue_command(
            resource_type="device",
            resource_id="PUMP-1",
            topic="iot/devices/PUMP-1/control",
            payload={"command": "start"},
        )

        with self.assertRaises(MqttCommandQueueFull):
            bus.enqueue_command(
                resource_type="device",
                resource_id="PUMP-2",
                topic="iot/devices/PUMP-2/control",
                payload={"command": "start"},
            )

        self.assertEqual(len(self.redis.streams[bus.command_stream]), 1)

    def test_recovered_publishing_command_is_not_republished(self):
        request_id = self.web_bus.enqueue_command(
            resource_type="device",
            resource_id="VALVE-1",
            topic="iot/devices/VALVE-1/control",
            payload={"command": "toggle"},
        )
        self.web_bus.set_state(request_id, "publishing", terminal=False)
        self.web_bus.ensure_groups()
        # First consumer received the entry and then lost Redis/process state.
        self.web_bus.read_commands("old-runner", block_ms=1)
        claimed = self.runner_bus.claim_stale_commands(
            "new-runner", min_idle_ms=0
        )
        mqtt = FakeMqttService()
        worker = MqttCommandWorker(self.runner_bus, mqtt, threading.Event())

        self.assertEqual(len(claimed), 1)
        worker._process(*claimed[0], recovered=True)

        self.assertEqual(
            self.web_bus.get_request(request_id)["status"], "delivery_unknown"
        )
        self.assertEqual(mqtt.calls, [])

    def test_worker_claims_pending_created_after_startup(self):
        class GatedMqtt(FakeMqttService):
            def __init__(self):
                super().__init__()
                self.connected = threading.Event()

            def wait_until_connected(self, timeout=1):
                return self.connected.wait(timeout)

        mqtt = GatedMqtt()
        stop = threading.Event()
        worker = MqttCommandWorker(self.runner_bus, mqtt, stop)
        worker.start()
        deadline = time.monotonic() + 1
        while not self.redis.groups and time.monotonic() < deadline:
            time.sleep(0.001)

        request_id = self.web_bus.enqueue_command(
            resource_type="device",
            resource_id="VALVE-2",
            topic="iot/devices/VALVE-2/control",
            payload={"command": "toggle"},
        )
        self.web_bus.set_state(request_id, "publishing", terminal=False)
        # Simulate a previous consumer losing Redis/process state after delivery.
        self.web_bus.read_commands("failed-runner", block_ms=1)
        mqtt.connected.set()

        deadline = time.monotonic() + 1
        while (
            self.web_bus.get_request(request_id).get("status") != "delivery_unknown"
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        stop.set()
        worker.join(1)

        self.assertEqual(
            self.web_bus.get_request(request_id)["status"], "delivery_unknown"
        )
        self.assertEqual(mqtt.calls, [])

    def test_redis_failure_is_fail_closed(self):
        class BrokenRedis:
            def xlen(self, *args, **kwargs):
                import redis
                raise redis.ConnectionError("down")

            def pipeline(self, transaction=True):
                import redis
                raise redis.ConnectionError("down")

            def set(self, *args, **kwargs):
                import redis
                raise redis.ConnectionError("down")

        bus = MqttCommandBus(BrokenRedis(), prefix="test:mqtt")
        with self.assertRaises(MqttCommandBusUnavailable):
            bus.enqueue_command(
                resource_type="device",
                resource_id="PUMP-1",
                topic="iot/devices/PUMP-1/control",
                payload={"command": "start"},
            )

    def test_unknown_exec_result_does_not_revoke_reserved_check_code(self):
        class UnknownExecPipeline(_FakePipeline):
            def execute(self):
                # 模拟服务端已经执行 MULTI/EXEC，但响应在返回途中丢失。
                import redis
                raise redis.TimeoutError("EXEC result unknown")

        class UnknownExecRedis(FakeRedis):
            def pipeline(self, transaction=True):
                return UnknownExecPipeline(self)

        redis_client = UnknownExecRedis()
        bus = MqttCommandBus(redis_client, prefix="test:mqtt")

        with self.assertRaises(MqttCommandBusUnavailable):
            bus.enqueue_command(
                resource_type="device",
                resource_id="PUMP-2",
                topic="iot/devices/PUMP-2/control",
                payload={"command": "start"},
                require_device_ack=True,
            )

        # 不确定结果下宁可让孤立映射自然 TTL，也不能破坏可能已入流命令的 ACK。
        check_keys = [key for key in redis_client.values if ":check:" in key]
        self.assertEqual(len(check_keys), 1)
        self.assertEqual(len(redis_client.streams[bus.command_stream]), 1)

    def test_device_ack_cannot_be_overwritten_by_waiting_transition(self):
        request_id = self.web_bus.enqueue_command(
            resource_type="device",
            resource_id="PUMP-1",
            topic="iot/devices/PUMP-1/control",
            payload={"command": "start"},
            require_device_ack=True,
        )
        _message_id, fields = self._one_command()
        check_code = json.loads(fields["payload_json"])["check_code"]
        self.runner_bus.resolve_check_code("device", "PUMP-1", check_code)

        result = self.runner_bus.transition_if_pending(
            request_id, "awaiting_device_ack"
        )

        self.assertEqual(result["status"], "device_acked")

    def test_dead_letter_stream_has_a_configured_memory_bound(self):
        self.web_bus.dead_letter_inbound(
            topic="iot/sensors/S-1/data",
            payload=b"bad",
            reason="invalid payload",
            qos=1,
            message_id=7,
        )

        stream, options = self.redis.xadd_options[-1]
        self.assertEqual(stream, self.web_bus.dead_letter_stream)
        self.assertEqual(options["maxlen"], 10_000)
        self.assertTrue(options["approximate"])

    def test_dead_letter_payload_is_truncated_before_redis_storage(self):
        payload = b"x" * (64 * 1024 + 123)

        self.web_bus.dead_letter_inbound(
            topic="iot/sensors/S-1/data",
            payload=payload,
            reason="oversized poison",
            qos=1,
            message_id=8,
        )

        _message_id, fields = self.redis.streams[
            self.web_bus.dead_letter_stream
        ][-1]
        self.assertEqual(fields["payload_truncated"], "1")
        self.assertEqual(int(fields["original_size"]), len(payload))
        self.assertEqual(int(fields["stored_size"]), 64 * 1024)
        self.assertEqual(len(base64.b64decode(fields["payload_b64"])), 64 * 1024)

    def test_command_timeouts_are_strictly_bounded_before_enqueue(self):
        cases = (
            {"broker_ack_timeout": 0},
            {"broker_ack_timeout": float("inf")},
            {"device_ack_timeout": True},
            {"device_ack_timeout": MAX_DEVICE_ACK_TIMEOUT_SECONDS + 1},
            {"execute_within": 121},
        )
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.web_bus.enqueue_command(
                    resource_type="device",
                    resource_id="PUMP-TIMEOUT",
                    topic="iot/devices/PUMP-TIMEOUT/control",
                    payload={"command": "start"},
                    **extra,
                )
        self.assertEqual(self.redis.streams[self.web_bus.command_stream], [])

    def test_worker_rejects_tampered_unbounded_timeout(self):
        request_id = self.web_bus.enqueue_command(
            resource_type="device",
            resource_id="PUMP-TAMPERED",
            topic="iot/devices/PUMP-TAMPERED/control",
            payload={"command": "start"},
        )
        message_id, fields = self._one_command()
        fields["device_ack_timeout_ms"] = str(10**12)
        mqtt = FakeMqttService()
        worker = MqttCommandWorker(self.runner_bus, mqtt, threading.Event())

        worker._process(message_id, fields, recovered=False)

        self.assertEqual(self.web_bus.get_request(request_id)["status"], "rejected")
        self.assertEqual(mqtt.calls, [])

    def test_device_ack_wait_responds_to_runner_stop_without_acking_pending(self):
        request_id = self.web_bus.enqueue_command(
            resource_type="device",
            resource_id="PUMP-STOP",
            topic="iot/devices/PUMP-STOP/control",
            payload={"command": "start"},
            require_device_ack=True,
            device_ack_timeout=30,
        )
        message_id, fields = self._one_command()
        stop = threading.Event()
        worker = MqttCommandWorker(
            self.runner_bus,
            FakeMqttService(),
            stop,
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

        stop.set()
        thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(
            self.web_bus.get_request(request_id)["status"],
            "awaiting_device_ack",
        )
        self.assertTrue(self.redis.streams[self.web_bus.command_stream])

    def test_startup_recovery_waits_for_broker_before_publishing_queued(self):
        class GatedMqtt(FakeMqttService):
            def __init__(self):
                super().__init__()
                self.connected = threading.Event()

            def wait_until_connected(self, timeout=1):
                return self.connected.wait(timeout)

        request_id = self.web_bus.enqueue_command(
            resource_type="device",
            resource_id="PUMP-RECOVER",
            topic="iot/devices/PUMP-RECOVER/control",
            payload={"command": "start"},
        )
        self.web_bus.ensure_groups()
        self.web_bus.read_commands("old-runner", block_ms=1)
        mqtt = GatedMqtt()
        stop = threading.Event()
        worker = MqttCommandWorker(self.runner_bus, mqtt, stop)
        worker.start()

        time.sleep(0.05)
        self.assertEqual(self.web_bus.get_request(request_id)["status"], "queued")
        self.assertEqual(mqtt.calls, [])

        mqtt.connected.set()
        deadline = time.monotonic() + 1
        while (
            self.web_bus.get_request(request_id).get("status") != "broker_acked"
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        stop.set()
        worker.join(2)

        self.assertEqual(
            self.web_bus.get_request(request_id)["status"],
            "broker_acked",
        )
        self.assertEqual(len(mqtt.calls), 1)

    def test_runner_lease_enforces_one_owner_and_compare_release(self):
        self.assertTrue(self.web_bus.acquire_runner_lease("runner-a"))
        self.assertFalse(self.runner_bus.acquire_runner_lease("runner-b"))
        self.assertFalse(self.runner_bus.renew_runner_lease("runner-b"))
        self.assertTrue(self.web_bus.renew_runner_lease("runner-a"))
        self.assertFalse(self.runner_bus.release_runner_lease("runner-b"))
        self.assertTrue(self.web_bus.release_runner_lease("runner-a"))
        self.assertTrue(self.runner_bus.acquire_runner_lease("runner-b"))


class BackendMqttLifecycleTests(SimpleTestCase):
    @patch("platform_settings.models.PlatformConfig.objects.filter")
    def test_connection_config_is_loaded_in_one_queryset(self, filter_config):
        from services.mqtt_service import MQTTService

        values_list = filter_config.return_value.values_list
        values_list.return_value = [
            ("mqtt_broker", "broker.internal"),
            ("mqtt_port", 1884),
            ("mqtt_keepalive", 45),
            ("mqtt_username", "runner"),
            ("mqtt_password", "secret"),
        ]

        config = MQTTService()._load_connection_config()

        self.assertEqual(config["broker"], "broker.internal")
        self.assertEqual(config["port"], 1884)
        filter_config.assert_called_once()
        values_list.assert_called_once_with("key", "value")

    def test_sensors_app_ready_has_no_mqtt_side_effect(self):
        from sensors.apps import SensorsConfig

        with patch("services.mqtt_service.MQTTService.connect_async") as connect:
            SensorsConfig("sensors", __import__("sensors")).ready()
        connect.assert_not_called()

    def test_two_asgi_workers_start_neither_scheduler_nor_paho(self):
        """部署即使漏配环境变量，多 web worker 也不能取得运行所有权。"""
        import automation
        import sensors
        from automation.apps import AutomationConfig
        from sensors.apps import SensorsConfig

        for executable in ("gunicorn", "uvicorn"):
            with self.subTest(executable=executable), patch(
                "automation.apps.sys.argv",
                [executable, "config.asgi:application"],
            ), patch(
                "automation.scheduler.start_scheduler"
            ) as start_scheduler, patch(
                "services.mqtt_service.MQTTService.connect_async"
            ) as connect:
                # 两次 ready() 模拟同一部署里的两个独立 ASGI worker。
                for _ in range(2):
                    AutomationConfig("automation", automation).ready()
                    SensorsConfig("sensors", sensors).ready()

                start_scheduler.assert_not_called()
                connect.assert_not_called()

    def test_runserver_does_not_compete_with_mqtt_runner_scheduler(self):
        import automation
        from automation.apps import AutomationConfig

        with patch(
            "automation.apps.sys.argv", ["manage.py", "runserver"]
        ), patch(
            "automation.scheduler.sys.argv", ["manage.py", "runserver"]
        ), patch.dict(
            "os.environ", {"RUN_MAIN": "true"}, clear=False
        ), patch(
            "automation.scheduler.start_scheduler"
        ) as start_scheduler:
            AutomationConfig("automation", automation).ready()

        start_scheduler.assert_not_called()

    @patch("services.mqtt_service.mqtt.Client")
    def test_connect_timeout_keeps_async_retry_loop_running(self, client_cls):
        from services.mqtt_service import MQTTService

        subscriber = Mock()
        publisher = Mock()
        client_cls.side_effect = [subscriber, publisher]
        service = MQTTService()
        service._load_connection_config = Mock(return_value={
            "broker": "unavailable.invalid",
            "port": 1883,
            "keepalive": 60,
            "username": "",
            "password": "",
        })

        self.assertFalse(service.connect(timeout=0.01))

        self.assertEqual(client_cls.call_count, 2)
        client_cls.assert_any_call(
            client_id="spr-iot-platform-runner",
            clean_session=False,
            manual_ack=True,
        )
        publisher_id = service._publisher_client_id("spr-iot-platform-runner")
        client_cls.assert_any_call(
            client_id=publisher_id,
            clean_session=True,
            manual_ack=False,
        )
        self.assertNotEqual(publisher_id, "spr-iot-platform-runner")
        self.assertLessEqual(len(publisher_id.encode("utf-8")), 23)
        for client in (subscriber, publisher):
            client.connect_async.assert_called_once()
            client.loop_start.assert_called_once()
            client.loop_stop.assert_not_called()

    @patch("services.mqtt_service.mqtt.Client")
    def test_config_database_error_does_not_fall_back_to_local_broker(self, client_cls):
        from services.mqtt_service import MQTTService

        service = MQTTService()
        service._load_connection_config = Mock(side_effect=RuntimeError("db down"))

        self.assertFalse(service.connect_async())
        client_cls.assert_not_called()
        self.assertEqual(service.connection_state, "disconnected")
        self.assertIn("config_load_failed", service.last_error)

    @patch("services.mqtt_service.mqtt.Client")
    def test_empty_client_id_uses_stable_default(self, client_cls):
        from services.mqtt_service import MQTTService

        subscriber = Mock()
        publisher = Mock()
        client_cls.side_effect = [subscriber, publisher]
        service = MQTTService()
        service._load_connection_config = Mock(return_value={
            "broker": "broker",
            "port": 1883,
            "keepalive": 60,
            "username": "",
            "password": "",
        })
        with patch.dict("os.environ", {"MQTT_CLIENT_ID": ""}, clear=False):
            self.assertTrue(service.connect_async())

        self.assertEqual(client_cls.call_count, 2)
        client_cls.assert_any_call(
            client_id="spr-iot-platform-runner",
            clean_session=False,
            manual_ack=True,
        )
        client_cls.assert_any_call(
            client_id=service._publisher_client_id(
                "spr-iot-platform-runner"
            ),
            clean_session=True,
            manual_ack=False,
        )

    def test_runner_is_ready_only_after_both_mqtt_channels_connect(self):
        from services.mqtt_service import MQTTService

        service = MQTTService()
        subscriber = Mock()
        publisher = Mock()
        service.client = subscriber
        service.publisher_client = publisher

        with patch.object(service, "_publish_system_status"):
            service._on_connect(subscriber, None, {}, 0)
            self.assertFalse(service.is_connected)
            self.assertFalse(service.wait_until_connected(timeout=0))

            service._on_publisher_connect(publisher, None, {}, 0)
            self.assertTrue(service.is_connected)
            self.assertTrue(service.wait_until_connected(timeout=0))

            service._on_publisher_disconnect(publisher, None, 7)
            self.assertFalse(service.is_connected)
            self.assertFalse(service.wait_until_connected(timeout=0))

    def test_publish_wait_uses_dedicated_publisher_network_loop(self):
        import paho.mqtt.client as mqtt
        from services.mqtt_service import MQTTService

        service = MQTTService()
        subscriber = Mock()
        publisher = Mock()
        publish_info = Mock()
        publish_info.rc = mqtt.MQTT_ERR_SUCCESS
        publish_info.is_published.return_value = True
        publisher.publish.return_value = publish_info
        service.client = subscriber
        service.publisher_client = publisher
        service._subscriber_connected = True
        service._publisher_connected = True
        service._refresh_connection_readiness()

        result = service.publish_wait(
            "iot/devices/FV0103/control",
            {"opening": 60},
            qos=1,
            timeout=2,
        )

        self.assertEqual(result, (True, ""))
        subscriber.publish.assert_not_called()
        publisher.publish.assert_called_once()
        publish_info.wait_for_publish.assert_called_once_with(timeout=2.0)

    def test_publish_wait_rejects_unserializable_payload_before_paho(self):
        from services.mqtt_service import MQTTService

        service = MQTTService()
        subscriber = Mock()
        publisher = Mock()
        service.client = subscriber
        service.publisher_client = publisher
        service._subscriber_connected = True
        service._publisher_connected = True
        service._refresh_connection_readiness()

        result = service.publish_wait(
            "iot/devices/FV0103/control",
            {"invalid": object()},
            qos=1,
            timeout=2,
        )

        self.assertEqual(result, (False, "invalid_message"))
        publisher.publish.assert_not_called()

    @patch("services.mqtt_service.mqtt.Client", side_effect=ValueError("bad client"))
    def test_client_initialization_failure_is_retryable(self, client_cls):
        from services.mqtt_service import MQTTService

        service = MQTTService()
        service._load_connection_config = Mock(return_value={
            "broker": "broker",
            "port": 1883,
            "keepalive": 60,
            "username": "",
            "password": "",
        })

        self.assertFalse(service.connect_async())
        self.assertIsNone(service.client)
        self.assertIsNone(service.publisher_client)
        self.assertEqual(service.connection_state, "disconnected")
        self.assertIn("client_init_failed", service.last_error)


class InboundManualAckTests(SimpleTestCase):
    def _message(self):
        msg = Mock()
        msg.topic = "iot/sensors/S-1/data"
        msg.payload = b'{"sensor_id":"S-1","data":{"v":1},"timestamp":1}'
        msg.qos = 1
        msg.mid = 42
        return msg

    def test_successful_handler_is_acked_without_dead_letter(self):
        from services.mqtt_service import MQTTService

        service = MQTTService()
        handler = Mock(return_value=True)
        handler.__name__ = "successful_handler"
        service.register_handler("iot/sensors/+/data", handler)
        client = Mock()
        client.ack.return_value = 0
        bus = Mock()

        with patch(
            "services.mqtt_command_bus.get_mqtt_command_bus", return_value=bus
        ):
            service._on_message(client, None, self._message())

        client.ack.assert_called_once_with(42, 1)
        bus.dead_letter_inbound.assert_not_called()

    def test_failed_handler_is_dead_lettered_before_ack(self):
        from services.mqtt_service import MQTTService

        service = MQTTService()
        handler = Mock(return_value=False)
        handler.__name__ = "failed_handler"
        service.register_handler("iot/sensors/+/data", handler)
        client = Mock()
        client.ack.return_value = 0
        bus = Mock()

        with patch(
            "services.mqtt_command_bus.get_mqtt_command_bus", return_value=bus
        ):
            service._on_message(client, None, self._message())

        bus.dead_letter_inbound.assert_called_once()
        client.ack.assert_called_once_with(42, 1)

    def test_dead_letter_failure_keeps_message_unacked(self):
        from services.mqtt_service import MQTTService

        service = MQTTService()
        handler = Mock(return_value=False)
        handler.__name__ = "failed_handler"
        service.register_handler("iot/sensors/+/data", handler)
        client = Mock()
        bus = Mock()
        bus.dead_letter_inbound.side_effect = MqttCommandBusUnavailable("redis down")

        with patch(
            "services.mqtt_command_bus.get_mqtt_command_bus", return_value=bus
        ):
            service._on_message(client, None, self._message())

        client.ack.assert_not_called()

    @override_settings(MQTT_INBOUND_MAX_PAYLOAD_BYTES=16)
    def test_oversized_message_is_not_json_parsed_and_is_dead_lettered(self):
        from services.mqtt_service import MQTTService

        service = MQTTService()
        handler = Mock(return_value=True)
        handler.__name__ = "must_not_run"
        service.register_handler("iot/sensors/+/data", handler)
        client = Mock()
        client.ack.return_value = 0
        bus = Mock()
        message = self._message()
        message.payload = b"{" + b"x" * 100 + b"}"

        with patch(
            "services.mqtt_command_bus.get_mqtt_command_bus",
            return_value=bus,
        ):
            service._on_message(client, None, message)

        handler.assert_not_called()
        self.assertIn(
            "payload_too_large",
            bus.dead_letter_inbound.call_args.kwargs["reason"],
        )
        client.ack.assert_called_once_with(42, 1)


class StatusAckCommitOrderingTests(SimpleTestCase):
    @patch("services.sensors_service.sensor_upload_status_handlers._resolve_command_ack")
    @patch("services.sensors_service.sensor_upload_status_handlers.transaction.on_commit")
    @patch("services.sensors_service.sensor_upload_status_handlers._save_status", return_value=True)
    @patch("services.sensors_service.sensor_upload_status_handlers._get_sensor")
    def test_sensor_ack_is_registered_only_after_successful_save(
        self, get_sensor, _save, on_commit, resolve
    ):
        from services.sensors_service.sensor_upload_status_handlers import (
            handle_mqtt_status_message,
        )

        get_sensor.return_value = Mock(
            mqtt_topic_status="iot/sensors/S-1/status",
        )
        ok = handle_mqtt_status_message("iot/sensors/S-1/status", {
            "sensor_id": "S-1",
            "status": {"online": True},
            "event": "online",
            "timestamp": 1,
            "check_code": "123456",
        })

        self.assertTrue(ok)
        resolve.assert_not_called()
        callback = on_commit.call_args.args[0]
        callback()
        resolve.assert_called_once_with("S-1", "123456")

    @patch("services.devices_service.device_upload_status_handlers._resolve_command_ack")
    @patch("services.devices_service.device_upload_status_handlers.transaction.on_commit")
    @patch("services.devices_service.device_upload_status_handlers._save_device_status", return_value=True)
    @patch("services.devices_service.device_upload_status_handlers._get_device")
    def test_device_ack_is_registered_only_after_successful_save(
        self, get_device, _save, on_commit, resolve
    ):
        from services.devices_service.device_upload_status_handlers import (
            handle_mqtt_device_status_message,
        )

        get_device.return_value = Mock(
            mqtt_topic_data="iot/devices/D-1/status",
        )
        ok = handle_mqtt_device_status_message("iot/devices/D-1/status", {
            "device_id": "D-1",
            "status": {"power": True},
            "event": "state",
            "timestamp": 1,
            "check_code": "654321",
        })

        self.assertTrue(ok)
        resolve.assert_not_called()
        callback = on_commit.call_args.args[0]
        callback()
        resolve.assert_called_once_with("D-1", "654321")
