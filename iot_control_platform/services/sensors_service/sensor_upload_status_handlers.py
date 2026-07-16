"""
MQTT传感器状态接收解析程序
负责接收MQTT消息，解析传感器状态数据并存入数据库
符合 mqtt_status_form: sensor_id, event, status, check_code(可选), timestamp
若含 check_code 则调用 sensor_command_send_service 校验（用于 send_command_with_make_sure）
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional
from django.db import transaction
from sensors.models import Sensor, SensorStatusCollection

logger = logging.getLogger(__name__)


def _resolve_command_ack(sensor_id: str, check_code: str) -> None:
    try:
        from services.mqtt_command_bus import get_mqtt_command_bus
        get_mqtt_command_bus().resolve_check_code("sensor", sensor_id, check_code)
    except Exception as exc:
        # 状态已经提交，Redis 短暂故障不应造成重复落库。
        logger.error("传感器命令 ACK 写入 Redis 失败: %s", exc)


def handle_mqtt_status_message(topic: str, payload: Dict) -> bool:
    """
    处理MQTT传感器状态上报消息
    消息格式: {"sensor_id": "xxx", "status": {...}, "timestamp": xxx, "event": "xxx"}
    """
    try:
        if not _validate_message(payload):
            return False

        sensor_id = payload['sensor_id']
        check_code = (str(payload.get('check_code') or '')).strip() or None

        sensor = _get_sensor(sensor_id)
        if not sensor:
            logger.error(f"✗ 传感器不存在: {sensor_id}")
            return False

        event_name = payload['event']
        status_to_save = _extract_status_fields(sensor, payload)
        if not status_to_save:
            logger.error(f"✗ 未能从消息中提取状态数据: {sensor_id}")
            return False

        timestamp = _convert_timestamp(payload['timestamp'])
        success = _save_status(sensor, status_to_save, timestamp, event_name)

        if success:
            logger.info(f"✓ 状态保存成功 - 传感器: {sensor_id}, 状态: {status_to_save}")
            if check_code:
                # ACK 只能在状态合法且成功落库后完成；Redis 映射使 web worker
                # 与独立 mqtt_runner 之间不再依赖进程内 Event/dict。
                transaction.on_commit(
                    lambda: _resolve_command_ack(sensor_id, check_code)
                )
        return success

    except Exception as e:
        logger.exception(f"✗ 处理MQTT状态消息时发生异常: {e}")
        return False


def _validate_message(payload: Dict) -> bool:
    required_fields = ['sensor_id', 'status', 'timestamp', 'event']
    for field in required_fields:
        if field not in payload:
            logger.error(f"✗ 消息缺少必需字段: {field}")
            return False
    if not isinstance(payload['status'], dict):
        logger.error("✗ status字段必须是一个字典")
        return False
    if not isinstance(payload['timestamp'], (int, float)):
        logger.error("✗ timestamp字段必须是数字类型")
        return False
    if not isinstance(payload['event'], str):
        logger.error("✗ event字段必须是字符串类型")
        return False
    return True


def _get_sensor(sensor_id: str) -> Optional[Sensor]:
    try:
        return Sensor.objects.select_related('sensor_type').get(sensor_id=sensor_id)
    except Sensor.DoesNotExist:
        logger.warning(f"⚠ 传感器不存在: {sensor_id}")
        return None
    except Exception as e:
        logger.error(f"✗ 查询传感器失败: {sensor_id}, 错误: {e}")
        return None


def _extract_status_fields(sensor: Sensor, payload: Dict) -> Optional[Dict]:
    extracted_status = payload.get('status')
    if isinstance(extracted_status, dict):
        return extracted_status
    return None


def _convert_timestamp(ts) -> datetime:
    from django.utils import timezone as django_tz
    try:
        value = float(ts)
        if value >= 1e12:
            value = value / 1000.0
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (ValueError, TypeError, OSError) as e:
        logger.error(f"✗ 时间戳转换失败: {ts} ({type(ts).__name__}), {e}")
        return django_tz.now()


def _save_status(sensor: Sensor, status_dict: Dict, timestamp: datetime, event_name: str) -> bool:
    try:
        SensorStatusCollection.objects.create(
            sensor=sensor, data=status_dict, timestamp=timestamp, event_name=event_name
        )
        return True
    except Exception as e:
        logger.error(f"✗ 状态数据保存失败 - 传感器: {sensor.sensor_id}, 错误: {e}", exc_info=True)
        return False
