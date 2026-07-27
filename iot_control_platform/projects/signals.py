"""
监听 SensorData 落库，若该传感器已加入某些项目，则按各项目成员构造点位样本，
广播到对应的 projects.{project_id} 通道。

设备状态不在此另写信号：直接复用主层 DeviceStatusCollection 信号（consumer 订阅
devices.all 后按成员过滤转发），与 eb_plant 一致。

实时缓存说明：projects 不写 services.realtime 的全局 latest_values 缓存（那是按 point_id
索引的，与插件共享会互相覆盖）。projects 的 snapshot 改为现查 DB，这里只负责增量广播。
"""
import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from sensors.models import SensorData
from services.realtime import dispatch
from services.realtime.latest_values import build_point_sample

from .models import ProjectDeviceMember, ProjectSensorMember
from .realtime import publish_project_membership_changed

logger = logging.getLogger(__name__)


def _schedule_membership_refresh(project_ids):
    """事务提交后通知受影响项目；实时层故障不能反向导致成员写接口失败。"""
    project_ids = tuple(sorted({project_id for project_id in project_ids if project_id}))
    if not project_ids:
        return

    def _publish():
        for project_id in project_ids:
            try:
                publish_project_membership_changed(project_id)
            except Exception as exc:
                logger.warning(
                    "projects 成员变化广播失败 project_id=%s err=%s",
                    project_id,
                    exc,
                )

    transaction.on_commit(_publish)


def _remember_previous_project(sender, instance):
    """成员被移动到另一项目时，旧项目也必须刷新已有连接。"""
    previous_project_id = None
    if instance.pk:
        previous_project_id = (
            sender.objects.filter(pk=instance.pk)
            .values_list("project_id", flat=True)
            .first()
        )
    instance._realtime_previous_project_id = previous_project_id


@receiver(
    pre_save,
    sender=ProjectSensorMember,
    dispatch_uid="projects_remember_sensor_member_project",
)
@receiver(
    pre_save,
    sender=ProjectDeviceMember,
    dispatch_uid="projects_remember_device_member_project",
)
def on_project_member_pre_save(sender, instance, **kwargs):
    _remember_previous_project(sender, instance)


@receiver(
    post_save,
    sender=ProjectSensorMember,
    dispatch_uid="projects_refresh_sensor_membership",
)
@receiver(
    post_save,
    sender=ProjectDeviceMember,
    dispatch_uid="projects_refresh_device_membership",
)
def on_project_member_saved(sender, instance, **kwargs):
    if getattr(instance, "_skip_realtime_membership_refresh", False):
        return
    _schedule_membership_refresh(
        {
            instance.project_id,
            getattr(instance, "_realtime_previous_project_id", None),
        }
    )


@receiver(
    post_delete,
    sender=ProjectSensorMember,
    dispatch_uid="projects_refresh_deleted_sensor_membership",
)
@receiver(
    post_delete,
    sender=ProjectDeviceMember,
    dispatch_uid="projects_refresh_deleted_device_membership",
)
def on_project_member_deleted(sender, instance, **kwargs):
    _schedule_membership_refresh({instance.project_id})


@receiver(post_save, sender=SensorData, dispatch_uid="projects_ingest_sensor_data")
def on_sensor_data_saved(sender, instance: SensorData, created: bool, **kwargs):
    if not created:
        return
    try:
        # 同一 sensor 可能属于多个项目；同一项目内还可能被多个房间复用（多条成员）。
        members = ProjectSensorMember.objects.filter(
            sensor_id=instance.sensor_id, is_visible=True,
        ).select_related("sensor", "project")
        ts = instance.timestamp.timestamp() if instance.timestamp else None
        payloads = []
        # 按 (project_id, point_id) 去重：跨房间复用同一点位时只向该项目通道推一次
        seen = set()
        for m in members:
            key = (m.project_id, m.point_id)
            if key in seen:
                continue
            seen.add(key)
            sample = build_point_sample(
                m.point_id, instance.data, ts,
                plugin_code=m.project.code, binding=m,
            )
            payloads.append((m.project_id, sample.to_dict()))

        if not payloads:
            return

        def _publish():
            for project_id, payload in payloads:
                dispatch.publish_project_sample(project_id, payload)

        # 与主层一致：延迟到事务提交后再广播（回滚不推、不阻塞 INSERT）
        transaction.on_commit(_publish)
    except Exception as exc:
        logger.warning("projects 实时广播失败 sensor_id=%s err=%s", instance.sensor_id, exc)
