"""Redis-backed MQTT command/control bus.

Only the dedicated ``mqtt_runner`` process owns a Paho client.  Web workers,
admin actions and automation code enqueue commands here and wait for a result
stored in Redis.  Redis Streams provide consumer acknowledgement and crash
recovery; per-request hashes/lists make result delivery race-free across
processes.

This module deliberately uses redis-py rather than the Channels layer.  A
channel layer is an ephemeral fan-out transport (and remains the right tool
for WebSocket updates), while device commands need a durable consumer queue.
"""
from __future__ import annotations

import json
import base64
import logging
import math
import os
import secrets
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

import redis
from django.conf import settings

logger = logging.getLogger(__name__)


class MqttCommandBusError(RuntimeError):
    """Base error for the MQTT Redis bus."""


class MqttCommandBusUnavailable(MqttCommandBusError):
    """Redis is unavailable; callers must fail closed and not publish MQTT."""


class MqttCommandQueueFull(MqttCommandBusError):
    """The durable command stream reached its configured backlog limit."""


TERMINAL_STATES = frozenset({
    "broker_acked",
    "device_acked",
    "expired_before_publish",
    "broker_timeout",
    "broker_unavailable",
    "device_ack_timeout",
    "delivery_unknown",
    "rejected",
    "reload_applied",
    "reload_failed",
})

# 设备回传的 check_code 是比本地超时判断更强的事实证据。以下状态只表示
# “当时无法确认交付/设备确认”，允许后到的真实设备 ACK 将其升级。
DEVICE_ACK_SUPERSEDABLE_STATES = frozenset({
    "broker_timeout",  # 兼容修复前把 PUBACK 超时记成 broker_timeout 的历史请求
    "device_ack_timeout",
    "delivery_unknown",
})

DELIVERY_UNKNOWN_PUBLISH_ERRORS = frozenset({
    "puback_timeout",
    "puback_wait_exception",
    "publish_call_exception",
    # Paho 在 connected 检查与 publish() 之间断线时返回 MQTT_ERR_NO_CONN(4)；
    # QoS 1 消息可能已经进入客户端重发队列，不能断言 broker 未收到。
    "publish_rc_4",
})

REJECTED_PUBLISH_ERRORS = frozenset({
    "invalid_message",
    # Paho outgoing queue 已满时，本条消息没有进入 _out_messages。
    "publish_rc_15",
})

MIN_COMMAND_TIMEOUT_SECONDS = 0.1
MAX_BROKER_ACK_TIMEOUT_SECONDS = 30.0
MAX_DEVICE_ACK_TIMEOUT_SECONDS = 60.0
MAX_COMMAND_EXECUTE_WITHIN_SECONDS = 120.0


def validate_command_timeout(value, *, name: str, maximum: float) -> float:
    """严格限制 Redis stream 中可导致 worker 阻塞的时间值。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是数值")
    timeout = float(value)
    if (
        not math.isfinite(timeout)
        or timeout < MIN_COMMAND_TIMEOUT_SECONDS
        or timeout > maximum
    ):
        raise ValueError(
            f"{name} 必须在 {MIN_COMMAND_TIMEOUT_SECONDS:g} 到 {maximum:g} 秒之间"
        )
    return timeout


@dataclass(frozen=True)
class CommandResult:
    request_id: str
    status: str
    success: bool
    error: str = ""


def _now_ms() -> int:
    return int(time.time() * 1000)


class MqttCommandBus:
    """Synchronous redis-py facade shared by web and the runner."""

    def __init__(self, client=None, *, prefix: Optional[str] = None):
        self._client = client
        self.prefix = prefix or getattr(settings, "MQTT_BUS_PREFIX", "spr:iot:mqtt")
        self.command_stream = f"{self.prefix}:commands:v1"
        self.control_stream = f"{self.prefix}:control:v1"
        self.command_group = f"{self.prefix}:command-runner:v1"
        self.control_group = f"{self.prefix}:control-runner:v1"
        self.dead_letter_stream = f"{self.prefix}:inbound-deadletter:v1"
        self.result_ttl = int(getattr(settings, "MQTT_COMMAND_RESULT_TTL", 600))
        self.check_code_ttl = int(getattr(settings, "MQTT_CHECK_CODE_TTL", 120))
        self.command_stream_max_backlog = max(
            1,
            int(getattr(settings, "MQTT_COMMAND_STREAM_MAX_BACKLOG", 10_000)),
        )
        self.dead_letter_maxlen = max(
            1, int(getattr(settings, "MQTT_DEAD_LETTER_MAXLEN", 10_000))
        )
        self.dead_letter_max_payload_bytes = max(
            1,
            int(
                getattr(
                    settings,
                    "MQTT_DEAD_LETTER_MAX_PAYLOAD_BYTES",
                    64 * 1024,
                )
            ),
        )
        self.runner_status_key = f"{self.prefix}:runner:status:v1"
        self.runner_lease_key = f"{self.prefix}:runner:lease:v1"

    @property
    def client(self):
        if self._client is None:
            url = getattr(settings, "REDIS_URL", None)
            if not url:
                raise MqttCommandBusUnavailable("REDIS_URL 未配置")
            self._client = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=float(
                    getattr(settings, "MQTT_BUS_REDIS_CONNECT_TIMEOUT", 1.0)
                ),
                socket_timeout=float(
                    getattr(settings, "MQTT_BUS_REDIS_SOCKET_TIMEOUT", 2.0)
                ),
                health_check_interval=30,
            )
        return self._client

    def _request_key(self, request_id: str) -> str:
        return f"{self.prefix}:req:{request_id}"

    def _result_queue_key(self, request_id: str) -> str:
        return f"{self.prefix}:resultq:{request_id}"

    def _check_key(self, check_code: str) -> str:
        return f"{self.prefix}:check:{check_code}"

    @staticmethod
    def _decode_mapping(mapping: Dict[Any, Any]) -> Dict[str, str]:
        decoded = {}
        for key, value in (mapping or {}).items():
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            decoded[str(key)] = str(value)
        return decoded

    def _redis_call(self, operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (redis.RedisError, OSError, TimeoutError) as exc:
            raise MqttCommandBusUnavailable(str(exc)) from exc

    def _reserve_check_code(
        self,
        request_id: str,
        resource_type: str,
        resource_id: str,
        ttl: int,
    ) -> str:
        mapping = json.dumps({
            "request_id": request_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
        }, ensure_ascii=False, separators=(",", ":"))
        for _ in range(30):
            code = str(secrets.randbelow(900000) + 100000)
            reserved = self._redis_call(
                self.client.set,
                self._check_key(code),
                mapping,
                nx=True,
                ex=max(ttl, self.check_code_ttl),
            )
            if reserved:
                return code
        raise MqttCommandBusError("无法分配唯一 MQTT check_code")

    def enqueue_command(
        self,
        *,
        resource_type: str,
        resource_id: str,
        topic: str,
        payload: dict,
        require_device_ack: bool = False,
        broker_ack_timeout: float = 2.0,
        device_ack_timeout: float = 3.0,
        execute_within: Optional[float] = None,
        request_id: Optional[str] = None,
    ) -> str:
        """Atomically persist request state and append one command entry."""
        if resource_type not in {"sensor", "device"}:
            raise ValueError("resource_type 必须是 sensor 或 device")
        if not resource_id or not topic or not isinstance(payload, dict):
            raise ValueError("resource_id/topic/payload 无效")

        broker_ack_timeout = validate_command_timeout(
            broker_ack_timeout,
            name="broker_ack_timeout",
            maximum=MAX_BROKER_ACK_TIMEOUT_SECONDS,
        )
        device_ack_timeout = validate_command_timeout(
            device_ack_timeout,
            name="device_ack_timeout",
            maximum=MAX_DEVICE_ACK_TIMEOUT_SECONDS,
        )

        request_id = request_id or uuid.uuid4().hex
        created_at = _now_ms()
        if execute_within is None:
            execute_within = max(5.0, broker_ack_timeout + device_ack_timeout + 1.0)
        execute_within = validate_command_timeout(
            execute_within,
            name="execute_within",
            maximum=MAX_COMMAND_EXECUTE_WITHIN_SECONDS,
        )

        # 命令处理完成后会 XDEL，因此 XLEN 就是当前持久化积压量。这里是
        # 软上限：极端并发生产者可能小幅越界，但不会在 runner 离线时无界增长。
        backlog = int(
            self._redis_call(self.client.xlen, self.command_stream) or 0
        )
        if backlog >= self.command_stream_max_backlog:
            raise MqttCommandQueueFull(
                "MQTT 命令队列已满 "
                f"({backlog}/{self.command_stream_max_backlog})"
            )

        execute_before = created_at + int(max(1.0, execute_within) * 1000)
        confirmation = "device" if require_device_ack else "none"

        message = dict(payload)
        check_code = ""
        if require_device_ack:
            check_code = self._reserve_check_code(
                request_id,
                resource_type,
                resource_id,
                int(device_ack_timeout) + 30,
            )
            message["check_code"] = check_code

        state = {
            "request_id": request_id,
            "kind": "command",
            "status": "queued",
            "resource_type": resource_type,
            "resource_id": resource_id,
            "confirmation": confirmation,
            "check_code": check_code,
            "created_at_ms": str(created_at),
            "execute_before_ms": str(execute_before),
            "updated_at_ms": str(created_at),
            "error": "",
        }
        entry = {
            "v": "1",
            "kind": "command",
            "request_id": request_id,
            "created_at_ms": str(created_at),
            "execute_before_ms": str(execute_before),
            "resource_type": resource_type,
            "resource_id": resource_id,
            "topic": topic,
            "payload_json": json.dumps(
                message, ensure_ascii=False, separators=(",", ":")
            ),
            "qos": "1",
            "confirmation": confirmation,
            "broker_ack_timeout_ms": str(int(broker_ack_timeout * 1000)),
            "device_ack_timeout_ms": str(int(device_ack_timeout * 1000)),
        }

        try:
            pipe = self.client.pipeline(transaction=True)
            pipe.hset(self._request_key(request_id), mapping=state)
            pipe.expire(self._request_key(request_id), self.result_ttl)
            pipe.xadd(self.command_stream, entry)
            self._redis_call(pipe.execute)
        except Exception as exc:
            # EXEC 超时/断线并不表示服务端没有提交。此时撤销先前通过 SET NX
            # 预留的 check_code，会留下“命令已入流但设备 ACK 永远无法匹配”的
            # 更坏状态。映射即使最终没有对应命令也只会存活到 TTL。
            if isinstance(exc, (redis.RedisError, OSError, TimeoutError)):
                raise MqttCommandBusUnavailable(str(exc)) from exc
            raise
        return request_id

    def enqueue_reload(self) -> str:
        request_id = uuid.uuid4().hex
        now = _now_ms()
        state = {
            "request_id": request_id,
            "kind": "config.reload",
            "status": "queued",
            "created_at_ms": str(now),
            "updated_at_ms": str(now),
            "error": "",
        }
        entry = {
            "v": "1",
            "kind": "config.reload",
            "request_id": request_id,
            "created_at_ms": str(now),
        }
        pipe = self.client.pipeline(transaction=True)
        pipe.hset(self._request_key(request_id), mapping=state)
        pipe.expire(self._request_key(request_id), self.result_ttl)
        pipe.xadd(self.control_stream, entry)
        self._redis_call(pipe.execute)
        return request_id

    def get_request(self, request_id: str) -> Dict[str, str]:
        result = self._redis_call(self.client.hgetall, self._request_key(request_id))
        return self._decode_mapping(result)

    def set_state(
        self,
        request_id: str,
        status: str,
        *,
        error: str = "",
        terminal: Optional[bool] = None,
        extra: Optional[dict] = None,
    ) -> Dict[str, str]:
        terminal = status in TERMINAL_STATES if terminal is None else terminal
        mapping = {
            "status": status,
            "updated_at_ms": str(_now_ms()),
            "error": error or "",
        }
        if extra:
            mapping.update({key: str(value) for key, value in extra.items()})
        pipe = self.client.pipeline(transaction=True)
        pipe.hset(self._request_key(request_id), mapping=mapping)
        pipe.expire(self._request_key(request_id), self.result_ttl)
        if terminal:
            pipe.rpush(self._result_queue_key(request_id), status)
            pipe.expire(self._result_queue_key(request_id), self.result_ttl)
        self._redis_call(pipe.execute)
        return self.get_request(request_id)

    def complete_if_pending(
        self,
        request_id: str,
        status: str,
        *,
        error: str = "",
        replace_terminal_states: Iterable[str] = (),
    ) -> Dict[str, str]:
        """Set a terminal result unless another terminal result already won.

        WATCH makes the device-ACK-vs-timeout race deterministic without a
        server-side dependency or custom Redis module.  A caller may explicitly
        name inconclusive terminal states that stronger evidence can replace;
        ordinary callers preserve the original first-terminal-result-wins rule.
        """
        replace_terminal_states = frozenset(replace_terminal_states)
        key = self._request_key(request_id)
        while True:
            pipe = self.client.pipeline()
            try:
                pipe.watch(key)
                current = pipe.hget(key, "status")
                if isinstance(current, bytes):
                    current = current.decode("utf-8")
                if (
                    current in TERMINAL_STATES
                    and current not in replace_terminal_states
                ):
                    pipe.unwatch()
                    return self.get_request(request_id)
                pipe.multi()
                pipe.hset(key, mapping={
                    "status": status,
                    "updated_at_ms": str(_now_ms()),
                    "error": error or "",
                })
                pipe.expire(key, self.result_ttl)
                pipe.rpush(self._result_queue_key(request_id), status)
                pipe.expire(self._result_queue_key(request_id), self.result_ttl)
                self._redis_call(pipe.execute)
                return self.get_request(request_id)
            except redis.WatchError:
                continue
            except (redis.RedisError, OSError, TimeoutError) as exc:
                raise MqttCommandBusUnavailable(str(exc)) from exc
            finally:
                try:
                    pipe.reset()
                except Exception:
                    pass

    def transition_if_pending(
        self,
        request_id: str,
        status: str,
        *,
        extra: Optional[dict] = None,
    ) -> Dict[str, str]:
        """Move between non-terminal states without overwriting a fast ACK."""
        key = self._request_key(request_id)
        while True:
            pipe = self.client.pipeline()
            try:
                pipe.watch(key)
                current = pipe.hget(key, "status")
                if isinstance(current, bytes):
                    current = current.decode("utf-8")
                if current in TERMINAL_STATES:
                    pipe.unwatch()
                    return self.get_request(request_id)
                mapping = {
                    "status": status,
                    "updated_at_ms": str(_now_ms()),
                    "error": "",
                }
                if extra:
                    mapping.update({key: str(value) for key, value in extra.items()})
                pipe.multi()
                pipe.hset(key, mapping=mapping)
                pipe.expire(key, self.result_ttl)
                self._redis_call(pipe.execute)
                return self.get_request(request_id)
            except redis.WatchError:
                continue
            except (redis.RedisError, OSError, TimeoutError) as exc:
                raise MqttCommandBusUnavailable(str(exc)) from exc
            finally:
                try:
                    pipe.reset()
                except Exception:
                    pass

    def wait_result(
        self,
        request_id: str,
        timeout: float,
        *,
        stop_event: Optional[threading.Event] = None,
    ) -> Dict[str, str]:
        deadline = time.monotonic() + max(0.01, float(timeout))
        while True:
            current = self.get_request(request_id)
            if current.get("status") in TERMINAL_STATES:
                return current
            if stop_event is not None and stop_event.is_set():
                return current
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return current
            # Short blocking slices keep arbitrary caller timeouts compatible
            # with the finite Redis socket timeout and allow graceful shutdown.
            self._redis_call(
                self.client.blpop,
                self._result_queue_key(request_id),
                timeout=min(1.0, remaining),
            )

    def resolve_check_code(
        self,
        resource_type: str,
        resource_id: str,
        check_code: str,
    ) -> bool:
        if not check_code:
            return False
        raw = self._redis_call(self.client.get, self._check_key(check_code))
        if not raw:
            logger.warning(
                "check_code 未找到或已过期 type=%s id=%s code=%s",
                resource_type,
                resource_id,
                check_code,
            )
            return False
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            mapping = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("check_code 映射损坏 code=%s", check_code)
            return False
        if (
            mapping.get("resource_type") != resource_type
            or mapping.get("resource_id") != resource_id
        ):
            logger.warning(
                "check_code 资源不匹配 code=%s expected=%s/%s actual=%s/%s",
                check_code,
                mapping.get("resource_type"),
                mapping.get("resource_id"),
                resource_type,
                resource_id,
            )
            return False
        request_id = mapping.get("request_id")
        if not request_id:
            return False
        result = self.complete_if_pending(
            request_id,
            "device_acked",
            replace_terminal_states=DEVICE_ACK_SUPERSEDABLE_STATES,
        )
        if result.get("status") != "device_acked":
            # 映射可能暂时无法升级（例如请求已明确 rejected）。保留到 TTL，
            # 避免一次竞态回调永久吃掉后续可用于诊断/重试的真实设备 ACK。
            logger.warning(
                "check_code 未能升级请求状态 code=%s request_id=%s status=%s",
                check_code,
                request_id,
                result.get("status", "missing"),
            )
            return False
        self._redis_call(self.client.delete, self._check_key(check_code))
        return True

    def ensure_groups(self) -> None:
        for stream, group in (
            (self.command_stream, self.command_group),
            (self.control_stream, self.control_group),
        ):
            try:
                self.client.xgroup_create(stream, group, id="0-0", mkstream=True)
            except redis.ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise MqttCommandBusUnavailable(str(exc)) from exc
            except (redis.RedisError, OSError, TimeoutError) as exc:
                raise MqttCommandBusUnavailable(str(exc)) from exc

    def read_commands(self, consumer: str, *, block_ms: int = 1000):
        result = self._redis_call(
            self.client.xreadgroup,
            self.command_group,
            consumer,
            {self.command_stream: ">"},
            count=1,
            block=block_ms,
        )
        return self._flatten_stream_messages(result)

    def read_controls(self, consumer: str, *, block_ms: int = 1000):
        result = self._redis_call(
            self.client.xreadgroup,
            self.control_group,
            consumer,
            {self.control_stream: ">"},
            count=10,
            block=block_ms,
        )
        return self._flatten_stream_messages(result)

    @classmethod
    def _flatten_stream_messages(cls, response) -> list[Tuple[str, Dict[str, str]]]:
        messages = []
        for _stream, entries in response or []:
            for message_id, fields in entries:
                if isinstance(message_id, bytes):
                    message_id = message_id.decode("utf-8")
                messages.append((str(message_id), cls._decode_mapping(fields)))
        return messages

    def ack_command(self, message_id: str) -> None:
        pipe = self.client.pipeline(transaction=True)
        pipe.xack(self.command_stream, self.command_group, message_id)
        pipe.xdel(self.command_stream, message_id)
        self._redis_call(pipe.execute)

    def ack_control(self, message_id: str) -> None:
        pipe = self.client.pipeline(transaction=True)
        pipe.xack(self.control_stream, self.control_group, message_id)
        pipe.xdel(self.control_stream, message_id)
        self._redis_call(pipe.execute)

    def claim_stale_commands(
        self,
        consumer: str,
        *,
        min_idle_ms: int = 10_000,
        count: int = 20,
    ) -> list[Tuple[str, Dict[str, str]]]:
        try:
            response = self.client.xautoclaim(
                self.command_stream,
                self.command_group,
                consumer,
                min_idle_ms,
                "0-0",
                count=count,
            )
        except redis.ResponseError as exc:
            # Older Redis servers may not support XAUTOCLAIM. New messages are
            # still processed; surface a warning rather than killing runner.
            logger.warning("Redis 不支持 XAUTOCLAIM，跳过 pending 回收: %s", exc)
            return []
        except (redis.RedisError, OSError, TimeoutError) as exc:
            raise MqttCommandBusUnavailable(str(exc)) from exc
        entries = response[1] if response and len(response) > 1 else []
        return self._flatten_stream_messages([(self.command_stream, entries)])

    def claim_stale_controls(
        self,
        consumer: str,
        *,
        min_idle_ms: int = 10_000,
        count: int = 20,
    ) -> list[Tuple[str, Dict[str, str]]]:
        try:
            response = self.client.xautoclaim(
                self.control_stream,
                self.control_group,
                consumer,
                min_idle_ms,
                "0-0",
                count=count,
            )
        except redis.ResponseError as exc:
            logger.warning("Redis 不支持 control XAUTOCLAIM: %s", exc)
            return []
        except (redis.RedisError, OSError, TimeoutError) as exc:
            raise MqttCommandBusUnavailable(str(exc)) from exc
        entries = response[1] if response and len(response) > 1 else []
        return self._flatten_stream_messages([(self.control_stream, entries)])

    def touch_runner_status(self, **values) -> None:
        mapping = {key: str(value) for key, value in values.items() if value is not None}
        mapping["heartbeat_at_ms"] = str(_now_ms())
        pipe = self.client.pipeline(transaction=True)
        pipe.hset(self.runner_status_key, mapping=mapping)
        pipe.expire(
            self.runner_status_key,
            int(getattr(settings, "MQTT_RUNNER_STATUS_TTL", 15)),
        )
        self._redis_call(pipe.execute)

    def acquire_runner_lease(self, owner: str) -> bool:
        """竞争唯一 runner 租约；同一时刻只允许一个 MQTT/调度器所有者。"""
        if not owner:
            raise ValueError("runner lease owner 不能为空")
        ttl = max(10, int(getattr(settings, "MQTT_RUNNER_LEASE_TTL", 20)))
        return bool(self._redis_call(
            self.client.set,
            self.runner_lease_key,
            owner,
            nx=True,
            ex=ttl,
        ))

    def renew_runner_lease(self, owner: str) -> bool:
        """仅当前所有者可续租，防止旧 runner 复活后覆盖新所有者。"""
        ttl = max(10, int(getattr(settings, "MQTT_RUNNER_LEASE_TTL", 20)))
        key = self.runner_lease_key
        while True:
            pipe = self.client.pipeline()
            try:
                pipe.watch(key)
                current = pipe.get(key)
                if isinstance(current, bytes):
                    current = current.decode("utf-8")
                if current != owner:
                    pipe.unwatch()
                    return False
                pipe.multi()
                pipe.expire(key, ttl)
                self._redis_call(pipe.execute)
                return True
            except redis.WatchError:
                continue
            except (redis.RedisError, OSError, TimeoutError) as exc:
                raise MqttCommandBusUnavailable(str(exc)) from exc
            finally:
                try:
                    pipe.reset()
                except Exception:
                    pass

    def release_runner_lease(self, owner: str) -> bool:
        """优雅退出时按所有者比较后释放；崩溃时由 TTL 自动回收。"""
        key = self.runner_lease_key
        while True:
            pipe = self.client.pipeline()
            try:
                pipe.watch(key)
                current = pipe.get(key)
                if isinstance(current, bytes):
                    current = current.decode("utf-8")
                if current != owner:
                    pipe.unwatch()
                    return False
                pipe.multi()
                pipe.delete(key)
                self._redis_call(pipe.execute)
                return True
            except redis.WatchError:
                continue
            except (redis.RedisError, OSError, TimeoutError) as exc:
                raise MqttCommandBusUnavailable(str(exc)) from exc
            finally:
                try:
                    pipe.reset()
                except Exception:
                    pass

    def dead_letter_inbound(
        self,
        *,
        topic: str,
        payload: bytes,
        reason: str,
        qos: int,
        message_id: int,
    ) -> str:
        """Persist an inbound message that could not be processed.

        The MQTT callback may ACK only after this succeeds.  A replay/cleanup
        command is intentionally separate from the hot callback path.
        """
        raw_payload = bytes(payload or b"")
        original_size = len(raw_payload)
        payload_truncated = original_size > self.dead_letter_max_payload_bytes
        stored_payload = raw_payload[:self.dead_letter_max_payload_bytes]
        entry = {
            "v": "1",
            "topic": topic,
            "payload_b64": base64.b64encode(stored_payload).decode("ascii"),
            "payload_truncated": "1" if payload_truncated else "0",
            "original_size": str(original_size),
            "stored_size": str(len(stored_payload)),
            "reason": (reason or "processing_failed")[:1000],
            "qos": str(qos),
            "mqtt_mid": str(message_id),
            "failed_at_ms": str(_now_ms()),
        }
        # 死信必须可诊断，但不能在持续毒消息下无限吃满 Redis 内存。
        result = self._redis_call(
            self.client.xadd,
            self.dead_letter_stream,
            entry,
            maxlen=self.dead_letter_maxlen,
            approximate=True,
        )
        return result.decode("utf-8") if isinstance(result, bytes) else str(result)

    def get_runner_status(self) -> Dict[str, str]:
        return self._decode_mapping(
            self._redis_call(self.client.hgetall, self.runner_status_key)
        )


class MqttCommandWorker(threading.Thread):
    """Single runner-side worker consuming and publishing queued commands."""

    def __init__(self, bus: MqttCommandBus, mqtt_service, stop_event: threading.Event):
        super().__init__(name="MqttCommandWorker", daemon=True)
        self.bus = bus
        self.mqtt_service = mqtt_service
        self.stop_event = stop_event
        self.consumer = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _publish_failure_status(error: str) -> str:
        if error == "not_connected":
            return "broker_unavailable"
        if error in REJECTED_PUBLISH_ERRORS:
            return "rejected"
        if error in DELIVERY_UNKNOWN_PUBLISH_ERRORS:
            return "delivery_unknown"
        return "broker_timeout"

    def _await_device_confirmation(
        self,
        request_id: str,
        *,
        timeout_seconds: float,
        broker_delivery: str,
        broker_error: str = "",
        wait_started_at_ms: Optional[int] = None,
    ) -> Optional[Dict[str, str]]:
        """等待真实设备 ACK，并保留 broker 交付是否确定的语义。

        ``None`` 表示 runner 正在停止，stream entry 必须保持 pending 供下个
        runner 恢复。设备 ACK 可以与状态迁移、超时判定并发；Redis WATCH 保证
        更强的 ``device_acked`` 事实不会被本地超时覆盖。
        """
        started_at_ms = int(wait_started_at_ms or _now_ms())
        extra = {
            "broker_delivery": broker_delivery,
            "broker_error": broker_error,
            "device_ack_wait_started_at_ms": started_at_ms,
        }
        if broker_delivery == "acked":
            # 保留旧字段，兼容已经运行中的 runner 所留下的 awaiting 状态。
            extra["broker_acked_at_ms"] = started_at_ms
        result = self.bus.transition_if_pending(
            request_id,
            "awaiting_device_ack",
            extra=extra,
        )
        if result.get("status") != "device_acked":
            elapsed = max(0.0, (_now_ms() - started_at_ms) / 1000.0)
            result = self.bus.wait_result(
                request_id,
                timeout=max(0.01, timeout_seconds - elapsed),
                stop_event=self.stop_event,
            )
        if self.stop_event.is_set():
            return None
        if result.get("status") == "device_acked":
            return result

        if broker_delivery == "unknown":
            error = "MQTT 交付结果不确定，且设备未在期限内确认"
            if broker_error:
                error = f"{error}: {broker_error}"
            return self.bus.complete_if_pending(
                request_id,
                "delivery_unknown",
                error=error,
            )
        return self.bus.complete_if_pending(
            request_id,
            "device_ack_timeout",
            error="broker 已接收命令，但设备未在期限内确认",
        )

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.bus.ensure_groups()
                # queued 的 pending 命令只有 broker 已连接时才能安全恢复；否则
                # 启动阶段会被错误标记 broker_unavailable 并永久 XDEL。
                if not self.mqtt_service.wait_until_connected(timeout=1.0):
                    continue
                for message_id, fields in self.bus.claim_stale_commands(self.consumer):
                    if self.stop_event.is_set():
                        return
                    self._process(message_id, fields, recovered=True)
                break
            except MqttCommandBusUnavailable as exc:
                logger.warning("MQTT 命令总线不可用，等待恢复: %s", exc)
                self.stop_event.wait(1)

        last_claim = 0.0
        while not self.stop_event.is_set():
            if not self.mqtt_service.wait_until_connected(timeout=1.0):
                continue
            try:
                now = time.monotonic()
                if now - last_claim >= 5:
                    last_claim = now
                    for message_id, fields in self.bus.claim_stale_commands(
                        self.consumer
                    ):
                        self._process(message_id, fields, recovered=True)
                messages = self.bus.read_commands(self.consumer, block_ms=1000)
                for message_id, fields in messages:
                    self._process(message_id, fields, recovered=False)
            except MqttCommandBusUnavailable as exc:
                logger.warning("读取 MQTT 命令流失败，稍后重试: %s", exc)
                self.stop_event.wait(1)
            except Exception:
                logger.exception("MQTT 命令 worker 未捕获异常")
                self.stop_event.wait(1)

    def _process(self, message_id: str, fields: Dict[str, str], *, recovered: bool) -> None:
        request_id = fields.get("request_id", "")
        if not request_id:
            logger.error("MQTT 命令缺少 request_id stream_id=%s", message_id)
            self.bus.ack_command(message_id)
            return
        state = self.bus.get_request(request_id)
        status = state.get("status", "queued")
        if status in TERMINAL_STATES:
            self.bus.ack_command(message_id)
            return
        if recovered and status == "publishing":
            if fields.get("confirmation") != "device":
                self.bus.complete_if_pending(
                    request_id,
                    "delivery_unknown",
                    error="runner 在 MQTT publish 临界区退出，未自动重发非幂等命令",
                )
                self.bus.ack_command(message_id)
                return
            try:
                timeout_seconds = validate_command_timeout(
                    int(fields.get("device_ack_timeout_ms") or 3000) / 1000.0,
                    name="device_ack_timeout",
                    maximum=MAX_DEVICE_ACK_TIMEOUT_SECONDS,
                )
            except (TypeError, ValueError):
                self.bus.complete_if_pending(
                    request_id,
                    "rejected",
                    error="非法 device_ack_timeout",
                )
                self.bus.ack_command(message_id)
                return
            result = self._await_device_confirmation(
                request_id,
                timeout_seconds=timeout_seconds,
                broker_delivery="unknown",
                broker_error="runner 在 MQTT publish 临界区退出",
            )
            if result is None:
                return
            self.bus.ack_command(message_id)
            return
        if recovered and status == "awaiting_device_ack":
            try:
                timeout_seconds = validate_command_timeout(
                    int(fields.get("device_ack_timeout_ms") or 3000) / 1000.0,
                    name="device_ack_timeout",
                    maximum=MAX_DEVICE_ACK_TIMEOUT_SECONDS,
                )
                wait_started_at_ms = int(
                    state.get("device_ack_wait_started_at_ms")
                    or state.get("broker_acked_at_ms")
                    or _now_ms()
                )
            except (TypeError, ValueError):
                self.bus.complete_if_pending(
                    request_id,
                    "rejected",
                    error="非法 device_ack_timeout",
                )
                self.bus.ack_command(message_id)
                return
            result = self._await_device_confirmation(
                request_id,
                timeout_seconds=timeout_seconds,
                broker_delivery=state.get("broker_delivery") or "acked",
                broker_error=state.get("broker_error", ""),
                wait_started_at_ms=wait_started_at_ms,
            )
            if result is None:
                return
            self.bus.ack_command(message_id)
            return

        try:
            execute_before = int(fields.get("execute_before_ms") or 0)
        except (TypeError, ValueError):
            execute_before = 0
        if execute_before and _now_ms() > execute_before:
            self.bus.complete_if_pending(
                request_id, "expired_before_publish", error="命令已超过执行截止时间"
            )
            self.bus.ack_command(message_id)
            return

        try:
            payload = json.loads(fields.get("payload_json") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("payload 不是 JSON 对象")
            broker_timeout = validate_command_timeout(
                int(fields.get("broker_ack_timeout_ms") or 2000) / 1000.0,
                name="broker_ack_timeout",
                maximum=MAX_BROKER_ACK_TIMEOUT_SECONDS,
            )
            device_timeout = validate_command_timeout(
                int(fields.get("device_ack_timeout_ms") or 3000) / 1000.0,
                name="device_ack_timeout",
                maximum=MAX_DEVICE_ACK_TIMEOUT_SECONDS,
            )
            self.bus.set_state(request_id, "publishing", terminal=False)
            published, error = self.mqtt_service.publish_wait(
                fields.get("topic", ""),
                payload,
                qos=int(fields.get("qos") or 1),
                timeout=broker_timeout,
            )
            if not published:
                terminal_status = self._publish_failure_status(error)
                if (
                    fields.get("confirmation") == "device"
                    and terminal_status == "delivery_unknown"
                ):
                    result = self._await_device_confirmation(
                        request_id,
                        timeout_seconds=device_timeout,
                        broker_delivery="unknown",
                        broker_error=error,
                    )
                    if result is None:
                        return
                    self.bus.ack_command(message_id)
                    return
                self.bus.complete_if_pending(request_id, terminal_status, error=error)
                self.bus.ack_command(message_id)
                return

            if fields.get("confirmation") != "device":
                self.bus.complete_if_pending(request_id, "broker_acked")
                self.bus.ack_command(message_id)
                return

            result = self._await_device_confirmation(
                request_id,
                timeout_seconds=device_timeout,
                broker_delivery="acked",
            )
            if result is None:
                return
            self.bus.ack_command(message_id)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self.bus.complete_if_pending(request_id, "rejected", error=str(exc))
            self.bus.ack_command(message_id)
        except MqttCommandBusUnavailable:
            # Leave stream entry pending. A recovered worker can inspect the
            # persisted state and apply the delivery-unknown rule if needed.
            raise
        except Exception as exc:
            logger.exception("执行 MQTT 命令失败 request_id=%s", request_id)
            try:
                self.bus.complete_if_pending(
                    request_id, "delivery_unknown", error=str(exc)
                )
                self.bus.ack_command(message_id)
            except MqttCommandBusUnavailable:
                raise


_default_bus: Optional[MqttCommandBus] = None
_default_bus_lock = threading.Lock()


def get_mqtt_command_bus() -> MqttCommandBus:
    global _default_bus
    if _default_bus is None:
        with _default_bus_lock:
            if _default_bus is None:
                _default_bus = MqttCommandBus()
    return _default_bus
