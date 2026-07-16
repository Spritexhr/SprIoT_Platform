"""
主模型 post_save → channel layer 的桥。

M2 阶段覆盖：
- SensorData：每条数据落库后按 sensor_id 广播到 sensors.{id} + sensors.all
- SensorStatusCollection：每条状态落库后按 sensor_id 广播，附带 is_online / last_seen

后续 milestone 会在此追加 DeviceStatusCollection、AutomationRule。

关键点：
- 信号 handler 在 ORM 事务内同步执行。我们统一用 transaction.on_commit
  延迟到事务提交后再 group_send：保证回滚不推消息，且不阻塞 INSERT 事务。
- 主层不 import 任何 plugins.*（解耦红线）。插件如需自定义流，
  在各自 plugins/<code>/signals.py 独立挂 receiver。
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from automation.models import AutomationRule, ControlScheme
from devices.models import Device, DeviceStatusCollection
from sensors.models import Sensor, SensorData, SensorStatusCollection

from . import dispatch

log = logging.getLogger(__name__)


def _ts(dt) -> float | None:
    """datetime → epoch 秒；None 透传。"""
    if dt is None:
        return None
    try:
        return dt.timestamp()
    except Exception:
        return None


@receiver(post_save, sender=SensorData, dispatch_uid="rt_sensor_data")
def on_sensor_data(sender, instance: SensorData, created: bool, **kwargs):
    if not created:
        return
    # 在事务内取 sensor_id 是安全的：SensorData 一定有 sensor FK
    sensor_id = instance.sensor.sensor_id
    payload = {
        "sensor_id": sensor_id,
        "data": instance.data,
        "timestamp": _ts(instance.timestamp),
        "received_at": _ts(instance.received_at),
    }
    transaction.on_commit(lambda: dispatch.publish_sensor_data(sensor_id, payload))


@receiver(post_save, sender=SensorStatusCollection, dispatch_uid="rt_sensor_status")
def on_sensor_status(sender, instance: SensorStatusCollection, created: bool, **kwargs):
    if not created:
        return
    # 父对象先于记录写入更新；重新查询可避免并发上报时使用旧的关联对象缓存。
    sensor = Sensor.objects.only('sensor_id', 'is_online', 'last_seen').get(pk=instance.sensor_id)
    sensor_id = sensor.sensor_id
    # SensorStatusCollection.save() 已在同一事务中更新在线状态。
    payload = {
        "sensor_id": sensor_id,
        "event": instance.event_name or "",
        "status": instance.data,
        "timestamp": _ts(instance.timestamp),
        "received_at": _ts(instance.received_at),
        "is_online": bool(sensor.is_online),
        "last_seen": _ts(sensor.last_seen),
    }
    transaction.on_commit(lambda: dispatch.publish_sensor_status(sensor_id, payload))


@receiver(post_save, sender=DeviceStatusCollection, dispatch_uid="rt_device_status")
def on_device_status(sender, instance: DeviceStatusCollection, created: bool, **kwargs):
    if not created:
        return
    # 父对象先于记录写入更新；重新查询保证广播的是数据库权威状态。
    device = Device.objects.only('device_id', 'is_online', 'last_seen').get(pk=instance.device_id)
    device_id = device.device_id
    # DeviceStatusCollection.save() 已在同一事务中更新在线状态。
    payload = {
        "device_id": device_id,
        "event": instance.event_name or "",
        "status": instance.data,
        "timestamp": _ts(instance.timestamp),
        "received_at": _ts(instance.received_at),
        "is_online": bool(device.is_online),
        "last_seen": _ts(device.last_seen),
    }
    transaction.on_commit(lambda: dispatch.publish_device_status(device_id, payload))


@receiver(post_save, sender=AutomationRule, dispatch_uid="rt_automation_rule")
def on_automation_rule(sender, instance: AutomationRule, created: bool, **kwargs):
    """规则增/改都广播——前端按 id 找到本地对象 patch 字段。"""
    payload = {
        "id": instance.id,
        "name": instance.name,
        "script_id": instance.script_id,
        "project": instance.project_id,
        "section": instance.section_id,
        "is_launched": bool(instance.is_launched),
        "process_status": instance.process_status,
        "error_message": instance.error_message or "",
        "poll_interval": instance.poll_interval,
        "last_run_time": instance.last_run_time.isoformat() if instance.last_run_time else None,
        "updated_at": instance.updated_at.isoformat() if instance.updated_at else None,
        "created": bool(created),
    }
    transaction.on_commit(lambda: dispatch.publish_automation_rule(payload))


@receiver(post_save, sender=ControlScheme, dispatch_uid="rt_control_scheme")
def on_control_scheme(sender, instance: ControlScheme, created: bool, **kwargs):
    """PI / PID 等结构化控制方案的运行态，供 P&ID 控制图元实时展示。"""
    update_fields = kwargs.get("update_fields")
    visible_fields = {"name", "control_type", "is_enabled", "status"}
    if not created and update_fields is not None and visible_fields.isdisjoint(update_fields):
        # 调度器每拍回写 PV / 输出值，图元不展示这些字段，避免无意义广播。
        return
    payload = {
        "id": instance.id,
        "name": instance.name,
        "project": instance.project_id,
        "section": instance.section_id,
        "control_type": instance.control_type,
        "control_type_display": instance.get_control_type_display(),
        "is_enabled": bool(instance.is_enabled),
        "status": instance.status,
        "status_display": instance.get_status_display(),
        "error_message": instance.error_message or "",
        "last_run_time": instance.last_run_time.isoformat() if instance.last_run_time else None,
        "last_pv": instance.last_pv,
        "last_output": instance.last_output,
        "updated_at": instance.updated_at.isoformat() if instance.updated_at else None,
        "created": bool(created),
    }
    transaction.on_commit(lambda: dispatch.publish_control_scheme(payload))
