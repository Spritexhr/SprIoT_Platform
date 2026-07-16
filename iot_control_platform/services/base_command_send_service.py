"""
命令发送服务基类
提供传感器和设备共用的命令发送、校验码确认等逻辑
子类只需指定 model_class 和 id_field_name 即可复用全部功能
"""
import json
import logging
from typing import Any, Dict, Optional

from django.conf import settings

from services.mqtt_command_bus import (
    MAX_BROKER_ACK_TIMEOUT_SECONDS,
    MAX_DEVICE_ACK_TIMEOUT_SECONDS,
    MqttCommandBusUnavailable,
    get_mqtt_command_bus,
    validate_command_timeout,
)

logger = logging.getLogger(__name__)


def _coerce_mqtt_message(raw: Any, command_name: str) -> Optional[Dict]:
    """把命令定义里的 mqtt_message 规范化成 dict。
    历史数据可能把它存成了 JSON 字符串（前端编辑器直接保存了字符串）；这里兜底解析。
    返回 dict；解析失败或类型不对返回 None（调用方 logger.error + return False）。
    """
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            logger.error("命令 %s 的 mqtt_message 不是合法 JSON: %r", command_name, raw)
            return None
        if not isinstance(parsed, dict):
            logger.error("命令 %s 的 mqtt_message 解析后不是对象: %r", command_name, parsed)
            return None
        return parsed
    logger.error("命令 %s 的 mqtt_message 类型不合法 (%s): %r", command_name, type(raw).__name__, raw)
    return None


class BaseCommandSendService:
    """
    命令发送服务基类
    子类必须设置:
        model_class: Django 模型类
        id_field_name: 模型中 ID 字段名（如 'sensor_id' 或 'device_id'）
        type_field_name: 模型中类型关联字段名（如 'sensor_type' 或 'device_type'）
    """

    model_class = None
    id_field_name = None
    type_field_name = None

    def __init__(self, mqtt_service=None):
        # 参数仅为兼容旧构造调用。backend 不再持有 Paho client；所有命令必须
        # 经 Redis Streams 交给唯一 mqtt_runner，Redis 故障时 fail closed。
        self.mqtt_service = None

    def set_mqtt_service(self, mqtt_service):
        """旧 API 兼容占位；禁止重新建立进程内 MQTT 直连路径。"""
        logger.debug("忽略 set_mqtt_service：命令统一经 Redis MQTT command bus")

    def _get_object(self, object_id: str):
        """根据 ID 获取模型实例"""
        return self.model_class.objects.get(**{self.id_field_name: object_id})

    def _get_commands(self, obj) -> Dict:
        """获取对象的可用控制命令"""
        type_obj = getattr(obj, self.type_field_name, None)
        if not type_obj:
            return {}
        return getattr(type_obj, 'commands', None) or {}

    def _publish_command(
        self,
        object_id: str,
        command_payload: Dict,
        *,
        require_device_ack: bool = False,
        timeout: float = 3.0,
    ) -> bool:
        """入 Redis Streams 并等待 runner 的 broker/设备确认。"""
        try:
            obj = self._get_object(object_id)
        except self.model_class.DoesNotExist:
            logger.error(f"{self.id_field_name}={object_id} 不存在")
            return False

        control_topic = obj.mqtt_topic_control
        if not control_topic:
            logger.error(f"{self.id_field_name}={object_id} 未配置控制主题")
            return False

        try:
            bus = get_mqtt_command_bus()
            resource_type = "sensor" if self.id_field_name == "sensor_id" else "device"
            broker_timeout = float(
                getattr(settings, "MQTT_COMMAND_BROKER_ACK_TIMEOUT", 2.0)
            )
            broker_timeout = validate_command_timeout(
                broker_timeout,
                name="broker_ack_timeout",
                maximum=MAX_BROKER_ACK_TIMEOUT_SECONDS,
            )
            device_timeout = validate_command_timeout(
                timeout,
                name="device_ack_timeout",
                maximum=MAX_DEVICE_ACK_TIMEOUT_SECONDS,
            )
            wait_timeout = broker_timeout + (
                device_timeout if require_device_ack else 0
            ) + 1.0
            request_id = bus.enqueue_command(
                resource_type=resource_type,
                resource_id=object_id,
                topic=control_topic,
                payload=command_payload,
                require_device_ack=require_device_ack,
                broker_ack_timeout=broker_timeout,
                device_ack_timeout=device_timeout,
                execute_within=wait_timeout,
            )
            result = bus.wait_result(request_id, timeout=wait_timeout)
            expected = "device_acked" if require_device_ack else "broker_acked"
            success = result.get("status") == expected
            if success:
                logger.info(
                    "命令确认成功 request_id=%s %s=%s status=%s",
                    request_id, self.id_field_name, object_id, expected,
                )
            else:
                logger.error(
                    "命令未确认 request_id=%s %s=%s status=%s error=%s",
                    request_id,
                    self.id_field_name,
                    object_id,
                    result.get("status", "unknown"),
                    result.get("error", ""),
                )
            return success
        except MqttCommandBusUnavailable as exc:
            logger.error("Redis MQTT 命令总线不可用，拒绝发送: %s", exc)
            return False
        except Exception as e:
            logger.error(f"命令发送异常 - {self.id_field_name}={object_id}: {e}", exc_info=True)
            return False

    @staticmethod
    def _apply_params_to_message(msg: dict, params: Dict) -> dict:
        """将 mqtt_message 中的占位符 {param_name} 替换为 params 中的实际值"""
        result = {}
        for key, value in msg.items():
            if isinstance(value, str):
                replaced = value
                for param_name, param_value in params.items():
                    placeholder = "{" + param_name + "}"
                    if placeholder in replaced:
                        if replaced == placeholder:
                            replaced = param_value
                            break
                        else:
                            replaced = replaced.replace(placeholder, str(param_value))
                result[key] = replaced
            elif isinstance(value, dict):
                result[key] = BaseCommandSendService._apply_params_to_message(value, params)
            else:
                result[key] = value
        return result

    @staticmethod
    def _strip_check_code(msg: dict) -> dict:
        """普通命令不携带 check_code。

        check_code 是服务层独占的「确认执行」校验码，只应由 _inject_check_code
        在 make_sure 模式下动态注入，绝不能来自命令模板。历史模板里残留的
        check_code（如占位的 "123456"）在这里一律剔除，避免误导设备/日志。
        """
        if 'check_code' not in msg:
            return msg
        return {k: v for k, v in msg.items() if k != 'check_code'}

    def send_command(
        self,
        object_id: str,
        command_name: str,
        params: Optional[Dict] = None
    ) -> bool:
        """根据类型定义发送自定义命令"""
        if not isinstance(command_name, str) or not command_name.strip():
            logger.error("command_name 必须是非空字符串")
            return False
        command_name = command_name.strip()
        if params is not None and not isinstance(params, dict):
            logger.error("命令参数 params 必须是字典")
            return False
        try:
            obj = self._get_object(object_id)
        except self.model_class.DoesNotExist:
            logger.error(f"{self.id_field_name}={object_id} 不存在")
            return False

        commands = self._get_commands(obj)
        if command_name not in commands:
            type_obj = getattr(obj, self.type_field_name, None)
            type_name = getattr(type_obj, 'name', '未知') if type_obj else '未知'
            logger.error(f"未定义的命令 '{command_name}' 对于类型 '{type_name}'")
            return False

        command_info = commands[command_name]
        mqtt_message = _coerce_mqtt_message(command_info.get('mqtt_message'), command_name)
        if mqtt_message is None:
            return False
        if params:
            mqtt_message = self._apply_params_to_message(mqtt_message, params)
        # 普通命令：直接下发，不带校验码（确认执行请走 with_make_sure）
        mqtt_message = self._strip_check_code(mqtt_message)
        return self._publish_command(object_id, mqtt_message)

    def send_command_with_make_sure(
        self,
        object_id: str,
        command_name: str,
        params: Optional[Dict] = None,
        *,
        timeout: int = 3,
    ) -> bool:
        """
        发送命令并等待回传带正确 check_code 的状态，确认命令被执行。

        Args:
            object_id: 设备/传感器 ID
            command_name: 命令名称
            params: 命令参数（可选）
            timeout: 等待秒数，默认 3 秒

        Returns:
            True: 在限制时间内收到正确 check_code
            False: 发送失败或超时未收到
        """
        if not isinstance(command_name, str) or not command_name.strip():
            logger.error("command_name 必须是非空字符串")
            return False
        command_name = command_name.strip()
        if params is not None and not isinstance(params, dict):
            logger.error("命令参数 params 必须是字典")
            return False
        try:
            obj = self._get_object(object_id)
        except self.model_class.DoesNotExist:
            logger.error(f"{self.id_field_name}={object_id} 不存在")
            return False

        commands = self._get_commands(obj)
        if command_name not in commands:
            type_obj = getattr(obj, self.type_field_name, None)
            type_name = getattr(type_obj, 'name', '未知') if type_obj else '未知'
            logger.error(f"未定义的命令 '{command_name}' 对于类型 '{type_name}'")
            return False

        command_info = commands[command_name]
        mqtt_message = _coerce_mqtt_message(command_info.get('mqtt_message'), command_name)
        if mqtt_message is None:
            return False
        if params:
            mqtt_message = self._apply_params_to_message(mqtt_message, params)
        # check_code 由 command bus 使用 Redis SET NX 分配并在发布前持久化；
        # status handler 成功落库后跨进程完成 request。
        mqtt_message = self._strip_check_code(mqtt_message)
        return self._publish_command(
            object_id,
            mqtt_message,
            require_device_ack=True,
            timeout=timeout,
        )

    def verify_check_code(self, object_id: str, check_code: str) -> bool:
        """兼容旧调用点；实际映射已迁移到 Redis。"""
        resource_type = "sensor" if self.id_field_name == "sensor_id" else "device"
        try:
            return get_mqtt_command_bus().resolve_check_code(
                resource_type, object_id, check_code
            )
        except MqttCommandBusUnavailable as exc:
            logger.error("Redis MQTT ACK 总线不可用: %s", exc)
            return False
