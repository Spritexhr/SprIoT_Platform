"""
MQTT设备状态接收解析程序
负责接收MQTT消息，解析设备状态数据并存入数据库
符合 mqtt_status_form: device_id, event, status, check_code(可选), timestamp
若含 check_code 则调用 device_command_send_service 校验（用于 send_command_with_make_sure）
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional
from django.db import InterfaceError, OperationalError, transaction
from devices.models import Device, DeviceStatusCollection
from services.mqtt_inbound import extract_message_id, topic_matches_binding

logger = logging.getLogger(__name__)


def _resolve_command_ack(device_id: str, check_code: str) -> None:
    try:
        from services.mqtt_command_bus import get_mqtt_command_bus
        get_mqtt_command_bus().resolve_check_code("device", device_id, check_code)
    except Exception as exc:
        logger.error("设备命令 ACK 写入 Redis 失败: %s", exc)


def handle_mqtt_device_status_message(topic: str, payload: Dict) -> bool:
    """
    处理MQTT设备状态上报消息
    消息格式: {"device_id": "xxx", "event": "xxx", "status": {...}, "timestamp": xxx}
    """
    try:
        if not _validate_message(payload):
            return False

        device_id = payload['device_id']
        check_code = (str(payload.get('check_code') or '')).strip() or None

        device = _get_device(device_id)
        if not device:
            return False  # _get_device 已记录日志

        if not topic_matches_binding(topic, device.mqtt_topic_data):
            logger.error(
                "✗ 设备状态 topic 与资源绑定不一致: device=%s expected=%s actual=%s",
                device_id,
                device.mqtt_topic_data,
                topic,
            )
            return False

        try:
            message_id = extract_message_id(payload)
        except ValueError as exc:
            logger.error("✗ MQTT 消息幂等键无效: %s", exc)
            return False

        event_name = payload['event']
        status_to_save = _extract_status_fields(payload)
        if not status_to_save:
            logger.error(f"✗ 未能从消息中提取状态数据: {device_id}")
            return False

        timestamp = _convert_timestamp(payload['timestamp'])
        success = _save_device_status(
            device,
            status_to_save,
            event_name,
            timestamp,
            message_id,
        )

        if success:
            logger.info(f"✓ 设备状态保存成功 - {device_id}, event={event_name}, 状态: {status_to_save}")
            if check_code:
                transaction.on_commit(
                    lambda: _resolve_command_ack(device_id, check_code)
                )
        return success

    except (OperationalError, InterfaceError):
        raise
    except Exception as e:
        logger.exception(f"✗ 处理MQTT设备状态消息时发生异常: {e}")
        return False


def _validate_message(payload: Dict) -> bool:
    required_fields = ['device_id', 'status', 'timestamp', 'event']
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


def _get_device(device_id: str) -> Optional[Device]:
    try:
        return Device.objects.select_related('device_type').get(device_id=device_id)
    except Device.DoesNotExist:
        logger.warning(f"⚠ 设备不存在: {device_id}")
        return None
    except (OperationalError, InterfaceError):
        raise
    except Exception as e:
        logger.error(f"✗ 查询设备失败: {device_id}, 错误: {e}")
        return None


def _extract_status_fields(payload: Dict) -> Optional[Dict]:
    extracted = payload.get('status')
    if isinstance(extracted, dict):
        return extracted
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


def _save_device_status(
    device: Device,
    status_data: Dict,
    event_name: str,
    timestamp: datetime,
    message_id: Optional[str] = None,
) -> bool:
    try:
        if message_id:
            _record, created = DeviceStatusCollection.objects.get_or_create(
                device=device,
                message_id=message_id,
                defaults={
                    "data": status_data,
                    "event_name": event_name,
                    "timestamp": timestamp,
                },
            )
            if not created:
                if (
                    _record.data != status_data
                    or _record.timestamp != timestamp
                    or _record.event_name != event_name
                ):
                    logger.error(
                        "✗ 设备状态 message_id 冲突 - 设备: %s, "
                        "message_id: %s",
                        device.device_id,
                        message_id,
                    )
                    return False
                logger.info(
                    "✓ 跳过重复设备状态 - 设备: %s, message_id: %s",
                    device.device_id,
                    message_id,
                )
        else:
            DeviceStatusCollection.objects.create(
                device=device,
                data=status_data,
                event_name=event_name,
                timestamp=timestamp,
            )
        return True
    except (OperationalError, InterfaceError):
        raise
    except Exception as e:
        logger.error(f"✗ 设备状态保存失败 - {device.device_id}, 错误: {e}", exc_info=True)
        return False
