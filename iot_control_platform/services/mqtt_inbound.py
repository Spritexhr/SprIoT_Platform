"""MQTT 入站消息的通用校验辅助函数。"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional


MAX_MESSAGE_ID_LENGTH = 128
_MESSAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")


def topic_matches_binding(received_topic: str, configured_topic: str) -> bool:
    """仅接受资源配置中明确绑定的完整 MQTT topic。"""
    return (
        isinstance(received_topic, str)
        and isinstance(configured_topic, str)
        and bool(configured_topic)
        and received_topic == configured_topic
    )


def extract_message_id(payload: Mapping[str, Any]) -> Optional[str]:
    """提取可选幂等键，兼容 ``message_id`` 与 ``message_uuid``。

    旧设备可以不发送这两个字段。若同时发送，则二者必须一致，避免同一条
    消息在不同固件版本中被赋予两个互相冲突的身份。
    """
    supplied = [
        payload[key]
        for key in ("message_id", "message_uuid")
        if key in payload and payload[key] is not None
    ]
    if not supplied:
        return None
    if any(not isinstance(value, str) for value in supplied):
        raise ValueError("message_id/message_uuid 必须是字符串")

    normalized = [value.strip() for value in supplied]
    if any(not value for value in normalized):
        raise ValueError("message_id/message_uuid 不能为空")
    if len(set(normalized)) != 1:
        raise ValueError("message_id 与 message_uuid 不一致")

    message_id = normalized[0]
    if len(message_id) > MAX_MESSAGE_ID_LENGTH:
        raise ValueError(
            f"message_id/message_uuid 不能超过 {MAX_MESSAGE_ID_LENGTH} 个字符"
        )
    # 生产 MySQL 默认使用大小写不敏感 collation。把协议限定为小写 ASCII，
    # 避免看似不同的 AbC/abc 在唯一索引中碰撞；UUID、小写十六进制和递增
    # 序列均可直接使用。
    if _MESSAGE_ID_RE.fullmatch(message_id) is None:
        raise ValueError(
            "message_id/message_uuid 只能使用小写字母、数字、点、下划线、"
            "冒号和连字符，且必须以字母或数字开头"
        )
    return message_id
