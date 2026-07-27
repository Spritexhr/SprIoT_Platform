"""
信号 → channel layer 的同步桥。

所有 group 命名和 group_send 包装都集中在这里：
- 信号 handler 只调 publish_*(...) 这一层业务 API
- 业务线程只写入有界、按资源合并的内存缓冲区
- 专用 worker 再用 async_to_sync(layer.group_send) 写 Redis
- channel_layer 缺失 / Redis 不可达时静默兜底，绝不抛出阻塞业务

Group 命名规则（按"资源 × 资源ID"）：
- sensors.{sensor_id}      单传感器订阅（数据 + 状态合并）
- sensors.all              全部传感器（列表/Dashboard 用）
- devices.{device_id}      单设备订阅
- devices.all              全部设备
- automation.rules         自动化脚本 / 结构化控制状态变更
- system.mqtt              MQTT broker 连接状态
- plugins.{plugin_code}    插件自定义流（如 EB 大屏）

Consumer 内必须有匹配的 broadcast_* handler：
- type=broadcast.sensor.data  ←→ async def broadcast_sensor_data(self, event)
- type=broadcast.sensor.status                  broadcast_sensor_status
- type=broadcast.device.status                  broadcast_device_status
- type=broadcast.automation.rule                broadcast_automation_rule
- type=broadcast.automation.control             broadcast_automation_control
- type=broadcast.system.mqtt                    broadcast_system_mqtt
- type=broadcast.plugin.sample                  broadcast_plugin_sample
"""
from __future__ import annotations

from collections import OrderedDict
import copy
import hashlib
import logging
import os
import re
import threading
import time
from typing import Any, Dict, Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder

log = logging.getLogger(__name__)


# 实时广播是“可由历史接口恢复”的瞬时通知，不能反向阻塞 MQTT 网络线程或
# 数据库事务提交。队列同时限制条目数和近似 JSON 字节，并按
# (group, type, resource) 合并未发送的旧状态。高优先级的成员/自动化/系统事件
# 使用独立保留区，避免被高频遥测饿死。
_DISPATCH_QUEUE_CAPACITY = int(
    getattr(settings, "REALTIME_DISPATCH_QUEUE_CAPACITY", 512)
)
_DISPATCH_QUEUE_MAX_BYTES = int(
    getattr(settings, "REALTIME_DISPATCH_QUEUE_MAX_BYTES", 16 * 1024 * 1024)
)
_HIGH_PRIORITY_TYPES = frozenset(
    {
        "broadcast.automation.rule",
        "broadcast.automation.control",
        "broadcast.system.mqtt",
        "broadcast.project.membership",
    }
)


def _message_size(group: str, message: Dict[str, Any]) -> int:
    size = len(group.encode("utf-8")) + 32
    encoder = DjangoJSONEncoder(ensure_ascii=False, separators=(",", ":"))
    for chunk in encoder.iterencode(message):
        size += len(chunk.encode("utf-8"))
        if size > _DISPATCH_QUEUE_MAX_BYTES:
            break
    return size


def _message_key(group: str, message: Dict[str, Any]):
    payload = message.get("payload")
    identity = None
    if isinstance(payload, dict):
        for field in (
            "sensor_id",
            "device_id",
            "project_id",
            "point_id",
            "id",
            "tag",
        ):
            value = payload.get(field)
            if value not in (None, ""):
                identity = str(value)
                break
    return (group, str(message.get("type") or ""), identity)


class _DispatchBuffer:
    """两级、有界、可合并的进程内实时通知缓冲区。"""

    def __init__(self, *, max_items: int, max_bytes: int):
        self.max_items = max(16, int(max_items))
        self.max_bytes = max(64 * 1024, int(max_bytes))
        self._high_max_items = max(4, self.max_items // 4)
        self._high_max_bytes = max(16 * 1024, self.max_bytes // 4)
        self._normal_max_items = self.max_items - self._high_max_items
        self._normal_max_bytes = self.max_bytes - self._high_max_bytes
        self._high = OrderedDict()
        self._normal = OrderedDict()
        self._high_bytes = 0
        self._normal_bytes = 0
        self._consecutive_high = 0
        self._max_high_burst = 8
        self._condition = threading.Condition()

    @staticmethod
    def _pop_oldest(items, current_bytes):
        _key, (_group, _message, size) = items.popitem(last=False)
        return current_bytes - size

    def put_nowait(self, item) -> bool:
        group, message = item
        try:
            # 调用方在入队后修改原字典不能绕过字节预算或改变待发送内容。
            message = copy.deepcopy(message)
            size = _message_size(group, message)
            key = _message_key(group, message)
        except Exception:
            return False

        high_priority = message.get("type") in _HIGH_PRIORITY_TYPES
        if high_priority:
            items = self._high
            byte_attr = "_high_bytes"
            max_items = self._high_max_items
            max_bytes = self._high_max_bytes
        else:
            items = self._normal
            byte_attr = "_normal_bytes"
            max_items = self._normal_max_items
            max_bytes = self._normal_max_bytes

        if size > max_bytes:
            return False

        with self._condition:
            current_bytes = getattr(self, byte_attr)
            previous = items.pop(key, None)
            if previous is not None:
                current_bytes -= previous[2]

            while items and (
                len(items) >= max_items or current_bytes + size > max_bytes
            ):
                current_bytes = self._pop_oldest(items, current_bytes)

            if len(items) >= max_items or current_bytes + size > max_bytes:
                # 仅在 lane 的下限配置比单条消息还小时发生；旧同 key 消息已经
                # 被移除，此时宁可丢弃过大的新状态，也不突破内存上限。
                setattr(self, byte_attr, current_bytes)
                return False

            items[key] = (group, message, size)
            setattr(self, byte_attr, current_bytes + size)
            self._condition.notify()
            return True

    def get(self):
        with self._condition:
            while not self._high and not self._normal:
                self._condition.wait()
            # 优先事件允许插队，但每 8 条至少放行 1 条普通遥测，避免持续的
            # 自动化/成员事件让 normal lane 永久饥饿。不同事件类型不承诺全局
            # FIFO；每个资源同类型的待发送状态仍按 key 合并为最新值。
            if self._high and (
                not self._normal
                or self._consecutive_high < self._max_high_burst
            ):
                _key, (group, message, size) = self._high.popitem(last=False)
                self._high_bytes -= size
                self._consecutive_high += 1
            else:
                _key, (group, message, size) = self._normal.popitem(last=False)
                self._normal_bytes -= size
                self._consecutive_high = 0
            return group, message

    def qsize(self) -> int:
        with self._condition:
            return len(self._high) + len(self._normal)

    def byte_size(self) -> int:
        with self._condition:
            return self._high_bytes + self._normal_bytes


_dispatch_queue = _DispatchBuffer(
    max_items=_DISPATCH_QUEUE_CAPACITY,
    max_bytes=_DISPATCH_QUEUE_MAX_BYTES,
)
_dispatch_thread: Optional[threading.Thread] = None
_dispatch_pid: Optional[int] = None
_dispatch_lock = threading.Lock()
_last_overflow_log_at = 0.0


# Channels group 名仅允许 ASCII 字母/数字/点/横线/下划线，且长度必须小于 100。
# 对本来就合法的历史 ID 保持原名；其余 ID 使用“可读片段 + 稳定哈希”，避免简单
# 替换造成 a:b 与 a-b 等不同资源碰撞。consumer 应复用下方 g_* 函数生成同一名称。
CHANNEL_GROUP_MAX_LENGTH = 100
_CHANNEL_GROUP_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_CHANNEL_GROUP_INVALID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_GROUP_DIGEST_LENGTH = 20


def make_resource_group(namespace: str, identifier: Any) -> str:
    """为动态资源 ID 生成确定、合法且碰撞风险可控的 Channels group 名。"""
    if (
        not isinstance(namespace, str)
        or not namespace
        or _CHANNEL_GROUP_RE.fullmatch(namespace) is None
    ):
        raise ValueError(f"非法 group namespace: {namespace!r}")

    prefix = f"{namespace}."
    max_component_length = CHANNEL_GROUP_MAX_LENGTH - 1 - len(prefix)
    if max_component_length < _GROUP_DIGEST_LENGTH:
        raise ValueError(f"group namespace 过长: {namespace!r}")

    raw = str(identifier)
    candidate = f"{prefix}{raw}"
    if (
        raw
        and len(candidate) < CHANNEL_GROUP_MAX_LENGTH
        and _CHANNEL_GROUP_RE.fullmatch(candidate) is not None
    ):
        return candidate

    digest_source = f"{namespace}\0{raw}".encode("utf-8", errors="surrogatepass")
    digest = hashlib.sha256(digest_source).hexdigest()[:_GROUP_DIGEST_LENGTH]
    hint = _CHANNEL_GROUP_INVALID_RE.sub("-", raw).strip("._-")
    hint_budget = max_component_length - len(digest) - 1
    trimmed_hint = hint[:max(0, hint_budget)].rstrip("._-")
    component = f"{trimmed_hint}-{digest}" if trimmed_hint else digest
    group = f"{prefix}{component}"

    # 这里是开发期不变量，而不是运行期容错；若常量被改坏应尽早暴露。
    if len(group) >= CHANNEL_GROUP_MAX_LENGTH or _CHANNEL_GROUP_RE.fullmatch(group) is None:
        raise AssertionError(f"生成了非法 Channels group: {group!r}")
    return group


# ---- group 命名 ----
def g_sensor_one(sensor_id: str) -> str:
    return make_resource_group("sensors", sensor_id)


def g_sensor_all() -> str:
    return "sensors.all"


def g_device_one(device_id: str) -> str:
    return make_resource_group("devices", device_id)


def g_device_all() -> str:
    return "devices.all"


def g_automation() -> str:
    return "automation.rules"


def g_mqtt_system() -> str:
    return "system.mqtt"


def g_plugin(plugin_code: str) -> str:
    return make_resource_group("plugins", plugin_code)


def g_project(project_id) -> str:
    return make_resource_group("projects", project_id)


# ---- 有界异步桥 ----
def _send_now(group: str, message: Dict[str, Any]) -> None:
    """在专用 worker 内实际写入 channel layer，异常绝不逃逸。"""
    try:
        layer = get_channel_layer()
        if layer is None:
            log.debug("[ws] channel layer 未配置，丢弃 group=%s", group)
            return
        async_to_sync(layer.group_send)(group, message)
    except Exception as exc:
        log.warning("[ws] group_send 失败 group=%s err=%s", group, exc)


def _dispatch_worker(work_queue: _DispatchBuffer) -> None:
    while True:
        group, message = work_queue.get()
        _send_now(group, message)


def _ensure_dispatch_worker() -> _DispatchBuffer:
    """懒启动线程，并在 Gunicorn fork 后为子进程重建 queue/thread。"""
    global _dispatch_pid, _dispatch_queue, _dispatch_thread

    current_pid = os.getpid()
    with _dispatch_lock:
        if _dispatch_pid != current_pid:
            _dispatch_pid = current_pid
            _dispatch_queue = _DispatchBuffer(
                max_items=_DISPATCH_QUEUE_CAPACITY,
                max_bytes=_DISPATCH_QUEUE_MAX_BYTES,
            )
            _dispatch_thread = None
        if _dispatch_thread is None or not _dispatch_thread.is_alive():
            _dispatch_thread = threading.Thread(
                target=_dispatch_worker,
                args=(_dispatch_queue,),
                name="realtime-dispatch",
                daemon=True,
            )
            _dispatch_thread.start()
        return _dispatch_queue


def _safe_send(group: str, message: Dict[str, Any]) -> None:
    """非阻塞入队；合并同资源旧状态并严格限制内存。"""
    global _last_overflow_log_at

    try:
        work_queue = _ensure_dispatch_worker()
        accepted = work_queue.put_nowait((group, message))
    except Exception as exc:
        log.warning("[ws] 实时广播入队失败 group=%s err=%s", group, exc)
        return
    if accepted:
        return

    now = time.monotonic()
    if now - _last_overflow_log_at >= 5:
        _last_overflow_log_at = now
        log.warning(
            "[ws] 实时广播被容量限制丢弃 capacity=%d max_bytes=%d",
            _DISPATCH_QUEUE_CAPACITY,
            _DISPATCH_QUEUE_MAX_BYTES,
        )


# ---- 业务级 publish ----
def publish_sensor_data(sensor_id: str, payload: dict) -> None:
    msg = {"type": "broadcast.sensor.data", "payload": payload}
    _safe_send(g_sensor_one(sensor_id), msg)
    _safe_send(g_sensor_all(), msg)


def publish_sensor_status(sensor_id: str, payload: dict) -> None:
    msg = {"type": "broadcast.sensor.status", "payload": payload}
    _safe_send(g_sensor_one(sensor_id), msg)
    _safe_send(g_sensor_all(), msg)


def publish_device_status(device_id: str, payload: dict) -> None:
    msg = {"type": "broadcast.device.status", "payload": payload}
    _safe_send(g_device_one(device_id), msg)
    _safe_send(g_device_all(), msg)


def publish_automation_rule(payload: dict) -> None:
    _safe_send(g_automation(), {"type": "broadcast.automation.rule", "payload": payload})


def publish_control_scheme(payload: dict) -> None:
    _safe_send(g_automation(), {"type": "broadcast.automation.control", "payload": payload})


def publish_mqtt_system(payload: dict) -> None:
    _safe_send(g_mqtt_system(), {"type": "broadcast.system.mqtt", "payload": payload})


def publish_plugin_sample(plugin_code: str, sample: dict) -> None:
    _safe_send(
        g_plugin(plugin_code),
        {"type": "broadcast.plugin.sample", "payload": sample},
    )


def publish_project_sample(project_id, sample: dict) -> None:
    """项目（场景）层传感器采样流，广播到 projects.{id} group。
    Consumer 内对应 broadcast_project_sample handler。"""
    _safe_send(
        g_project(project_id),
        {"type": "broadcast.project.sample", "payload": sample},
    )
