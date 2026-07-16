"""
平台默认配置常量
是 PlatformConfig 表的"种子值"——首次部署或新增配置项时，由 configure --init 写入。
所有运行期可调配置都列在这里，wizard 也以此为唯一信源。

字段语义：
- key:         PlatformConfig.key（小写下划线）
- default:     默认值，python 原生类型（str/int/float/list/dict/bool）
- category:    分组，便于 admin 列表排序
- description: 中文描述，wizard 提示也用它
- secret:      True 表示 wizard 输入时不回显（如密码）
- value_type:  显式声明类型，wizard 转换时使用；省略时按 default 推断
"""
import json
from typing import Any, Dict, List


class ConfigValidationError(ValueError):
    """已知平台配置的类型或取值不符合约束。"""


DEFAULT_CONFIGS: List[Dict[str, Any]] = [
    # ========== MQTT ==========
    {
        "key": "mqtt_broker",
        "default": "127.0.0.1",
        "category": "mqtt",
        "description": "MQTT/EMQX 服务器地址",
        "allow_blank": False,
        "strip": True,
    },
    {
        "key": "mqtt_port",
        "default": 1883,
        "category": "mqtt",
        "description": "MQTT 端口（EMQX 标准端口 1883）",
        "min_value": 1,
        "max_value": 65535,
    },
    {
        "key": "mqtt_keepalive",
        "default": 60,
        "category": "mqtt",
        "description": "MQTT 保活间隔（秒）",
        "min_value": 1,
        "max_value": 65535,
    },
    {
        "key": "mqtt_username",
        "default": "",
        "category": "mqtt",
        "description": "MQTT 用户名（可选，留空表示匿名连接）",
    },
    {
        "key": "mqtt_password",
        "default": "",
        "category": "mqtt",
        "description": "MQTT 密码（可选，留空表示无密码）",
        "secret": True,
    },

    # ========== 设备 ==========
    {
        "key": "device_offline_timeout",
        "default": 300,
        "category": "devices",
        "description": "设备离线判定超时（秒），无心跳则视为离线",
        "min_value": 1,
        "max_value": 86400,
    },
    {
        "key": "device_reconnect_attempts",
        "default": 3,
        "category": "devices",
        "description": "设备重连尝试次数",
        "min_value": 1,
        "max_value": 100,
    },
    {
        "key": "device_reconnect_interval",
        "default": 10,
        "category": "devices",
        "description": "设备重连间隔（秒）",
        "min_value": 1,
        "max_value": 3600,
    },

    # ========== 数据留存 ==========
    {
        "key": "sensor_data_retention_days",
        "default": 30,
        "category": "data_retention",
        "description": "传感器数据保留天数，超过则清理",
        "min_value": 1,
        "max_value": 3650,
    },
    {
        "key": "device_data_retention_days",
        "default": 30,
        "category": "data_retention",
        "description": "设备状态数据保留天数，超过则清理",
        "min_value": 1,
        "max_value": 3650,
    },
]


def get_default(key: str, fallback: Any = None) -> Any:
    """根据 key 查找默认值"""
    for item in DEFAULT_CONFIGS:
        if item["key"] == key:
            return item["default"]
    return fallback


def get_meta(key: str) -> Dict[str, Any]:
    """根据 key 查找完整 meta（含 description / category / secret）"""
    for item in DEFAULT_CONFIGS:
        if item["key"] == key:
            return dict(item)
    return {}


def validate_config_value(key: str, value: Any) -> Any:
    """严格校验一个已知配置值，未知自定义 key 原样放行。

    这里是 API、CLI 与种子写入的唯一规则源。使用 ``type(value) is`` 而非
    ``isinstance``，避免 Python 中 ``bool`` 作为 ``int`` 子类混入整数配置。
    返回值允许做轻量规范化（目前仅去除 broker 两端空白）。
    """
    meta = get_meta(key)
    if not meta:
        return value

    expected_type = meta.get("value_type", type(meta.get("default")))
    if type(value) is not expected_type:
        type_names = {
            str: "字符串",
            int: "整数",
            float: "浮点数",
            bool: "布尔值",
            list: "列表",
            dict: "对象",
        }
        expected_name = type_names.get(expected_type, expected_type.__name__)
        raise ConfigValidationError(f"{key} 必须是{expected_name}")

    if expected_type is str:
        if not meta.get("allow_blank", True) and not value.strip():
            raise ConfigValidationError(f"{key} 不能为空字符串")
        return value.strip() if meta.get("strip", False) else value

    if expected_type in (int, float):
        minimum = meta.get("min_value")
        maximum = meta.get("max_value")
        if minimum is not None and value < minimum:
            raise ConfigValidationError(f"{key} 不能小于 {minimum}")
        if maximum is not None and value > maximum:
            raise ConfigValidationError(f"{key} 不能大于 {maximum}")

    return value


def coerce_config_value(key: str, raw: Any) -> Any:
    """把 CLI 文本转换为已知配置的原生类型，再走统一严格校验。"""
    meta = get_meta(key)
    if not meta:
        return raw

    expected_type = meta.get("value_type", type(meta.get("default")))
    try:
        if expected_type is str:
            value = raw if type(raw) is str else str(raw)
        elif expected_type is bool:
            if type(raw) is bool:
                value = raw
            elif type(raw) is str and raw.strip().lower() in {
                "true", "1", "yes", "y", "on",
            }:
                value = True
            elif type(raw) is str and raw.strip().lower() in {
                "false", "0", "no", "n", "off",
            }:
                value = False
            else:
                raise ConfigValidationError(f"{key} 必须是布尔值")
        elif expected_type is int:
            if type(raw) is bool:
                raise ConfigValidationError(f"{key} 必须是整数")
            value = int(raw)
        elif expected_type is float:
            if type(raw) is bool:
                raise ConfigValidationError(f"{key} 必须是浮点数")
            value = float(raw)
        elif expected_type in (list, dict):
            value = json.loads(raw) if type(raw) is str else raw
        else:
            value = raw
    except ConfigValidationError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ConfigValidationError(
            f"{key} 无法转换为 {expected_type.__name__}: {raw!r}"
        ) from exc

    return validate_config_value(key, value)
