"""
信号 → channel layer 的同步桥。

所有 group 命名和 group_send 包装都集中在这里：
- 信号 handler 只调 publish_*(...) 这一层业务 API
- 内部统一用 async_to_sync(layer.group_send)
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

import hashlib
import logging
import re
from typing import Any, Dict

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

log = logging.getLogger(__name__)


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


# ---- 同步桥 ----
def _safe_send(group: str, message: Dict[str, Any]) -> None:
    layer = get_channel_layer()
    if layer is None:
        log.debug("[ws] channel layer 未配置，丢弃 group=%s", group)
        return
    try:
        async_to_sync(layer.group_send)(group, message)
    except Exception as exc:
        log.warning("[ws] group_send 失败 group=%s err=%s", group, exc)


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
