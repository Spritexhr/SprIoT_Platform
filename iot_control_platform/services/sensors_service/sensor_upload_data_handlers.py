"""
MQTT传感器数据接收解析程序
负责接收MQTT消息，解析传感器数据并存入数据库

实时广播（用于插件大屏 SSE）由各插件通过 SensorData post_save signal 自行接管，
本模块不直接耦合任何插件。
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional
from django.db import InterfaceError, OperationalError
from sensors.models import Sensor, SensorData
from services.mqtt_inbound import extract_message_id, topic_matches_binding

logger = logging.getLogger(__name__)


def handle_mqtt_data_message(topic: str, payload: Dict) -> bool:
    """
    处理MQTT数据上报消息
    消息格式: {"sensor_id": "xxx", "data": {...}, "timestamp": xxx}
    """
    try:
        if not _validate_message(payload):
            return False

        sensor_id = payload['sensor_id']
        sensor = _get_sensor(sensor_id)
        if not sensor:
            logger.error(f"✗ 传感器不存在: {sensor_id}")
            return False

        if not topic_matches_binding(topic, sensor.mqtt_topic_data):
            logger.error(
                "✗ 传感器数据 topic 与资源绑定不一致: sensor=%s expected=%s actual=%s",
                sensor_id,
                sensor.mqtt_topic_data,
                topic,
            )
            return False

        try:
            message_id = extract_message_id(payload)
        except ValueError as exc:
            logger.error("✗ MQTT 消息幂等键无效: %s", exc)
            return False

        data_to_save = _extract_data_fields(sensor, payload)
        if not data_to_save:
            logger.error(f"✗ 未能从消息中提取数据: {sensor_id}")
            return False

        timestamp = _convert_timestamp(payload['timestamp'])
        success = _save_data(sensor, data_to_save, timestamp, message_id)

        if success:
            logger.info(f"✓ 数据保存成功 - 传感器: {sensor_id}, 数据: {data_to_save}")

        return success

    except (OperationalError, InterfaceError):
        # 数据库暂时不可用时必须由 mqtt_service 保留未 ACK，等待 broker 重投。
        raise
    except Exception as e:
        logger.error(f"✗ 消息处理异常: {e}", exc_info=True)
        return False


def _validate_message(payload: Dict) -> bool:
    required_fields = ['sensor_id', 'data', 'timestamp']
    for field in required_fields:
        if field not in payload:
            logger.error(f"✗ 消息缺少必需字段: {field}")
            return False
    if not isinstance(payload['data'], dict):
        logger.error("✗ data字段必须是字典类型")
        return False
    if not isinstance(payload['timestamp'], (int, float)):
        logger.error("✗ timestamp必须是数字类型")
        return False
    return True


def _get_sensor(sensor_id: str) -> Optional[Sensor]:
    try:
        return Sensor.objects.select_related('sensor_type').get(sensor_id=sensor_id)
    except Sensor.DoesNotExist:
        logger.warning(f"⚠ 传感器不存在: {sensor_id}")
        return None
    except (OperationalError, InterfaceError):
        raise
    except Exception as e:
        logger.error(f"✗ 查询传感器失败: {sensor_id}, 错误: {e}")
        return None


def _extract_data_fields(sensor: Sensor, payload: Dict) -> Optional[Dict]:
    extracted_data = payload.get('data')
    if isinstance(extracted_data, dict):
        return extracted_data
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


def _save_data(
    sensor: Sensor,
    data_dict: Dict,
    timestamp: datetime,
    message_id: Optional[str] = None,
) -> bool:
    try:
        if message_id:
            _record, created = SensorData.objects.get_or_create(
                sensor=sensor,
                message_id=message_id,
                defaults={"data": data_dict, "timestamp": timestamp},
            )
            if not created:
                if (
                    _record.data != data_dict
                    or _record.timestamp != timestamp
                ):
                    logger.error(
                        "✗ 传感器数据 message_id 冲突 - 传感器: %s, "
                        "message_id: %s",
                        sensor.sensor_id,
                        message_id,
                    )
                    return False
                logger.info(
                    "✓ 跳过重复传感器数据 - 传感器: %s, message_id: %s",
                    sensor.sensor_id,
                    message_id,
                )
        else:
            SensorData.objects.create(
                sensor=sensor,
                data=data_dict,
                timestamp=timestamp,
            )
        return True
    except (OperationalError, InterfaceError):
        raise
    except Exception as e:
        logger.error(f"✗ 数据保存失败 - 传感器: {sensor.sensor_id}, 错误: {e}", exc_info=True)
        return False
