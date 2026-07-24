"""Paho MQTT 1.x / 2.x 兼容层。"""
import paho.mqtt.client as mqtt


def create_client(client_id: str) -> mqtt.Client:
    """优先使用 Paho 2.x 回调 API；旧版没有枚举时回退到原构造方式。"""
    callback_api = getattr(mqtt, "CallbackAPIVersion", None)
    if callback_api is not None:
        return mqtt.Client(callback_api.VERSION2, client_id=client_id)
    return mqtt.Client(client_id=client_id)


def reason_code_value(reason_code) -> int:
    """把 1.x 的 int 与 2.x 的 ReasonCode 统一成整数。"""
    return int(getattr(reason_code, "value", reason_code))
