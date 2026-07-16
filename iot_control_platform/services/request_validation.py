"""REST/MQTT 边界复用的严格参数解析。"""


def parse_boolean(value, *, field_name: str, default: bool = False) -> bool:
    """避免 ``bool('false') is True`` 导致真实设备副作用。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"{field_name} 必须是布尔值 true 或 false")
