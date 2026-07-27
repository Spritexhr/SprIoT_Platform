"""WebSocket 与实时广播的故障降级回归测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from .consumers import _BaseAuthedConsumer
from .dispatch import _DispatchBuffer, _safe_send, _send_now
from .middleware import (
    AuthenticationDependencyUnavailable,
    JwtAuthMiddleware,
)


class WebSocketConnectionDegradationTests(SimpleTestCase):
    @staticmethod
    def _consumer(groups):
        consumer = _BaseAuthedConsumer()
        consumer.scope = {
            "user": SimpleNamespace(is_authenticated=True),
        }
        consumer.groups_to_join = list(groups)
        consumer.channel_name = "test-channel"
        consumer.channel_layer = Mock()
        consumer.channel_layer.group_add = AsyncMock()
        consumer.channel_layer.group_discard = AsyncMock()
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer._send_initial = AsyncMock()
        return consumer

    def test_group_add_failure_closes_with_retryable_code_and_cleans_partial_join(self):
        consumer = self._consumer(["group.one", "group.two"])
        consumer.channel_layer.group_add.side_effect = [
            None,
            ConnectionError("redis unavailable"),
        ]

        async_to_sync(consumer.connect)()

        consumer.accept.assert_not_awaited()
        consumer.close.assert_awaited_once_with(code=1013)
        consumer.channel_layer.group_discard.assert_awaited_once_with(
            "group.one",
            "test-channel",
        )

    def test_authentication_dependency_failure_uses_retryable_close_code(self):
        consumer = self._consumer([])
        consumer.scope["auth_dependency_unavailable"] = True
        consumer.scope["user"] = SimpleNamespace(is_authenticated=False)

        async_to_sync(consumer.connect)()

        consumer.close.assert_awaited_once_with(code=1013)
        consumer.accept.assert_not_awaited()

    def test_hanging_group_operation_is_bounded_and_closes(self):
        consumer = self._consumer(["group.one"])

        async def never_returns(*_args):
            await asyncio.Event().wait()

        consumer.channel_layer.group_add.side_effect = never_returns
        with patch(
            "services.realtime.consumers.CHANNEL_OPERATION_TIMEOUT_SECONDS",
            0.01,
        ):
            async_to_sync(consumer.connect)()

        consumer.close.assert_awaited_once_with(code=1013)
        consumer.accept.assert_not_awaited()

    def test_initial_snapshot_failure_closes_and_discards_groups(self):
        consumer = self._consumer(["group.one"])
        consumer._send_initial.side_effect = RuntimeError("snapshot unavailable")

        async_to_sync(consumer.connect)()

        consumer.accept.assert_awaited_once()
        consumer.close.assert_awaited_once_with(code=1013)
        consumer.channel_layer.group_discard.assert_awaited_once_with(
            "group.one",
            "test-channel",
        )


class RealtimeDispatchDegradationTests(SimpleTestCase):
    def test_channel_layer_initialization_failure_never_escapes_business_path(self):
        with patch(
            "services.realtime.dispatch.get_channel_layer",
            side_effect=RuntimeError("bad channel configuration"),
        ):
            _send_now(
                "sensors.all",
                {"type": "broadcast.sensor.data", "payload": {"value": 1}},
            )

    def test_publish_path_only_enqueues_and_never_waits_for_redis(self):
        work_queue = Mock()
        with patch(
            "services.realtime.dispatch._ensure_dispatch_worker",
            return_value=work_queue,
        ), patch("services.realtime.dispatch._send_now") as send_now:
            _safe_send(
                "sensors.all",
                {"type": "broadcast.sensor.data", "payload": {"value": 1}},
            )

        work_queue.put_nowait.assert_called_once()
        send_now.assert_not_called()

    def test_pending_state_for_same_resource_is_coalesced_to_latest_value(self):
        buffer = _DispatchBuffer(max_items=32, max_bytes=1024 * 1024)
        first = {
            "type": "broadcast.sensor.data",
            "payload": {"sensor_id": "S-1", "value": 1},
        }
        latest = {
            "type": "broadcast.sensor.data",
            "payload": {"sensor_id": "S-1", "value": 2},
        }

        self.assertTrue(buffer.put_nowait(("sensors.all", first)))
        self.assertTrue(buffer.put_nowait(("sensors.all", latest)))

        self.assertEqual(buffer.qsize(), 1)
        _group, message = buffer.get()
        self.assertEqual(message["payload"]["value"], 2)

    def test_low_frequency_membership_event_has_reserved_priority_lane(self):
        buffer = _DispatchBuffer(max_items=32, max_bytes=1024 * 1024)
        membership = {
            "type": "broadcast.project.membership",
            "payload": {"project_id": 7},
        }
        self.assertTrue(buffer.put_nowait(("projects.7", membership)))
        for index in range(40):
            buffer.put_nowait(
                (
                    "sensors.all",
                    {
                        "type": "broadcast.sensor.data",
                        "payload": {
                            "sensor_id": f"S-{index}",
                            "value": index,
                        },
                    },
                )
            )

        group, message = buffer.get()
        self.assertEqual(group, "projects.7")
        self.assertEqual(message["type"], "broadcast.project.membership")
        self.assertLessEqual(buffer.qsize(), 24)

    def test_single_oversized_notification_is_rejected_by_byte_budget(self):
        buffer = _DispatchBuffer(max_items=32, max_bytes=64 * 1024)
        accepted = buffer.put_nowait(
            (
                "sensors.all",
                {
                    "type": "broadcast.sensor.data",
                    "payload": {
                        "sensor_id": "S-HUGE",
                        "blob": "x" * (64 * 1024),
                    },
                },
            )
        )

        self.assertFalse(accepted)
        self.assertEqual(buffer.qsize(), 0)

    def test_continuous_high_priority_events_cannot_starve_normal_lane(self):
        buffer = _DispatchBuffer(max_items=64, max_bytes=1024 * 1024)
        self.assertTrue(
            buffer.put_nowait(
                (
                    "sensors.all",
                    {
                        "type": "broadcast.sensor.data",
                        "payload": {"sensor_id": "S-NORMAL", "value": 1},
                    },
                )
            )
        )
        for index in range(16):
            self.assertTrue(
                buffer.put_nowait(
                    (
                        "automation.rules",
                        {
                            "type": "broadcast.automation.rule",
                            "payload": {"id": index},
                        },
                    )
                )
            )

        delivered = [buffer.get()[1]["type"] for _ in range(9)]

        self.assertIn("broadcast.sensor.data", delivered)
        self.assertEqual(delivered[-1], "broadcast.sensor.data")

    def test_enqueued_message_isolated_from_caller_mutation(self):
        buffer = _DispatchBuffer(max_items=32, max_bytes=1024 * 1024)
        message = {
            "type": "broadcast.sensor.data",
            "payload": {"sensor_id": "S-1", "value": 1},
        }
        self.assertTrue(buffer.put_nowait(("sensors.all", message)))

        message["payload"]["value"] = 999
        _group, queued = buffer.get()

        self.assertEqual(queued["payload"]["value"], 1)


class JwtDependencyDegradationTests(SimpleTestCase):
    def test_database_failure_is_marked_as_retryable_in_scope(self):
        captured = {}

        async def inner(scope, receive, send):
            captured.update(scope)

        async def receive():
            return {"type": "websocket.disconnect"}

        async def send(_message):
            return None

        scope = {
            "type": "websocket",
            "path": "/ws/sensors/",
            "query_string": b"token=valid-but-db-is-down",
        }
        with patch(
            "services.realtime.middleware._authenticate",
            AsyncMock(side_effect=AuthenticationDependencyUnavailable),
        ):
            async_to_sync(JwtAuthMiddleware(inner))(scope, receive, send)

        self.assertTrue(captured["auth_dependency_unavailable"])
        self.assertFalse(captured["user"].is_authenticated)
