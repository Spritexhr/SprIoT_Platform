"""
projects（项目/场景）API。

资源路由（平铺 + ?project= 过滤，与现有 eb_plant 风格一致）：
- /api/projects/                       项目 CRUD
- /api/projects/<pk>/layout/           项目展示骨架（分区 + 成员静态元信息）
- /api/projects/<pk>/snapshot/         项目当前实时值（现查 DB，不依赖进程缓存）
- /api/projects/<pk>/bindable_sources/ 可导入到本项目的全量传感器/设备
- /api/project_sections/?project=      分区 CRUD（含 reorder）
- /api/project_sensor_members/?project= 传感器成员 CRUD（含批量导入）
- /api/project_device_members/?project= 设备成员 CRUD（含批量导入）
- /api/project_views/?project=         视图 CRUD

实时增量推送由 WebSocket consumer 提供（projects.consumers.ProjectStreamConsumer，
URL: /ws/projects/<project_id>/）。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db.models import Count, IntegerField, OuterRef, Subquery, Value
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from devices.models import Device, DeviceStatusCollection
from sensors.models import Sensor, SensorData, SensorStatusCollection
from services.realtime.latest_values import build_point_sample

from .models import (
    Project,
    ProjectDeviceMember,
    ProjectSection,
    ProjectSensorMember,
    ProjectView,
)
from .serializers import (
    BindableDeviceSerializer,
    BindableSensorSerializer,
    ProjectDeviceMemberSerializer,
    ProjectSectionSerializer,
    ProjectSensorMemberSerializer,
    ProjectSerializer,
    ProjectViewSerializer,
)

log = logging.getLogger(__name__)

# 时序查询：单次返回上限，避免一次性把大量行扔给前端图表
DEFAULT_SERIES_LIMIT = 2000
MAX_SERIES_LIMIT = 10000
DEFAULT_SERIES_WINDOW = timedelta(hours=24)
MAX_SERIES_WINDOW = timedelta(days=31)


def _parse_dt(value, default, param_name):
    """严格解析 ISO 时间；只有参数完全缺省时才使用默认值。"""
    if value is None:
        return default
    try:
        dt = parse_datetime(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{param_name} 必须是合法的 ISO 8601 日期时间") from exc
    if dt is None:
        raise ValueError(f"{param_name} 必须是合法的 ISO 8601 日期时间")
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def _parse_series_limit(value):
    if value is None:
        return DEFAULT_SERIES_LIMIT
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError(f"limit 必须是 1 到 {MAX_SERIES_LIMIT} 之间的整数")
    limit = int(value)
    if limit < 1 or limit > MAX_SERIES_LIMIT:
        raise ValueError(f"limit 必须是 1 到 {MAX_SERIES_LIMIT} 之间的整数")
    return limit


def _truncate_window(qs, limit):
    """时间窗内行数超 limit 时仅保留最近 limit 条，按时间升序返回。"""
    total = qs.count()
    if total > limit:
        rows = list(qs.order_by("-timestamp", "-pk")[:limit])
        rows.reverse()
    else:
        rows = list(qs.order_by("timestamp", "pk"))
    return rows, total


def _related_project_count(model):
    """按 project_id 构造相关计数子查询，避免项目列表逐项 count。"""
    return (
        model.objects.filter(project_id=OuterRef("pk"))
        .order_by()
        .values("project_id")
        .annotate(total=Count("pk"))
        .values("total")
    )


def _with_project_counts(queryset):
    return queryset.annotate(
        _section_count=Coalesce(
            Subquery(
                _related_project_count(ProjectSection)[:1],
                output_field=IntegerField(),
            ),
            Value(0),
        ),
        _sensor_count=Coalesce(
            Subquery(
                _related_project_count(ProjectSensorMember)[:1],
                output_field=IntegerField(),
            ),
            Value(0),
        ),
        _device_count=Coalesce(
            Subquery(
                _related_project_count(ProjectDeviceMember)[:1],
                output_field=IntegerField(),
            ),
            Value(0),
        ),
        _view_count=Coalesce(
            Subquery(
                _related_project_count(ProjectView)[:1],
                output_field=IntegerField(),
            ),
            Value(0),
        ),
    )


# ---------- 权限 ----------

class _AdminWritePermission:
    """读用 IsAuthenticated，写用 IsAdminUser（与 eb_plant 一致）。"""

    _write_actions = ("create", "update", "partial_update", "destroy", "reorder")

    def get_permissions(self):
        if self.action in self._write_actions:
            return [IsAuthenticated(), IsAdminUser()]
        return [IsAuthenticated()]


# ---------- 实时聚合 helper（snapshot/consumer 共用） ----------

def _with_latest_sensor_data(queryset):
    latest = SensorData.objects.filter(
        sensor_id=OuterRef("sensor_id")
    ).order_by("-received_at", "-pk")
    return queryset.annotate(
        _snapshot_data=Subquery(latest.values("data")[:1]),
        _snapshot_timestamp=Subquery(latest.values("timestamp")[:1]),
    )


def _with_latest_device_status(queryset):
    latest = DeviceStatusCollection.objects.filter(
        device_id=OuterRef("device_id")
    ).order_by("-received_at", "-pk")
    return queryset.annotate(
        _snapshot_data=Subquery(latest.values("data")[:1]),
        _snapshot_event=Subquery(latest.values("event_name")[:1]),
        _snapshot_timestamp=Subquery(latest.values("timestamp")[:1]),
    )


def _sensor_sample(member: ProjectSensorMember, project_code: str) -> dict:
    """从主模型最近一条 SensorData 现查，构造点位样本（结构与 PointSample.to_dict 对齐）。
    无历史数据时返回 value=None 的占位样本，前端显示「--」。"""
    if hasattr(member, "_snapshot_timestamp"):
        timestamp = member._snapshot_timestamp
        data = member._snapshot_data if isinstance(member._snapshot_data, dict) else {}
    else:
        # 兼容独立调用 helper 的场景；项目 snapshot 的成员查询会走批量注解。
        last = (
            SensorData.objects.filter(sensor_id=member.sensor_id)
            .order_by("-received_at", "-pk")
            .first()
        )
        timestamp = last.timestamp if last else None
        data = last.data if (last and isinstance(last.data, dict)) else {}
    ts = timestamp.timestamp() if timestamp else None
    sample = build_point_sample(
        member.point_id, data, ts, plugin_code=project_code, binding=member,
    )
    return sample.to_dict()


def _device_state(member: ProjectDeviceMember) -> dict:
    """单个设备成员的当前状态：主模型最近一条 DeviceStatusCollection + 实时在线判定
    （Device.computed_is_online，跟设备管理页同口径）。"""
    device = member.device
    if hasattr(member, "_snapshot_timestamp"):
        timestamp = member._snapshot_timestamp
        data = member._snapshot_data
        event = member._snapshot_event
    else:
        # 兼容独立调用 helper 的场景；项目 snapshot 的成员查询会走批量注解。
        last = device.status_records.order_by("-received_at", "-pk").first()
        timestamp = last.timestamp if last else None
        data = last.data if last else None
        event = last.event_name if last else ""
    return {
        "device_id": device.device_id,
        "name": device.name,
        "tag": member.tag or device.device_id,
        "status": data if isinstance(data, dict) else {},
        "event": event or "",
        "is_online": bool(device.computed_is_online),
        "last_seen": device.last_seen.timestamp() if device.last_seen else None,
        "ts": timestamp.timestamp() if timestamp else None,
    }


def _build_layout(project: Project) -> dict:
    """展示骨架：有序房间(分区)，每个房间挂它的传感器/设备成员（静态元信息）。
    成员 section 必填，故无「未分组」段。"""
    sensor_members = (
        project.sensor_members.filter(is_visible=True)
        .select_related("sensor", "sensor__sensor_type", "section")
    )
    device_members = (
        project.device_members.filter(is_visible=True)
        .select_related("device", "device__device_type", "section")
    )
    s_data = ProjectSensorMemberSerializer(sensor_members, many=True).data
    d_data = ProjectDeviceMemberSerializer(device_members, many=True).data

    s_by_sec = defaultdict(list)
    for it in s_data:
        s_by_sec[it.get("section")].append(it)
    d_by_sec = defaultdict(list)
    for it in d_data:
        d_by_sec[it.get("section")].append(it)

    sections = []
    for sec in project.sections.all():
        sections.append({
            "id": sec.id,
            "name": sec.name,
            "sort_order": sec.sort_order,
            "sensors": s_by_sec.get(sec.id, []),
            "devices": d_by_sec.get(sec.id, []),
        })

    return {
        "project_id": project.id,
        "project_code": project.code,
        "name": project.name,
        "scene_type": project.scene_type,
        "view_settings": project.view_settings or {},
        "sections": sections,
    }


def build_project_snapshot(project: Project) -> dict:
    """项目当前实时快照：所有可见成员的最新值（现查 DB）。consumer 建连首发也复用此函数。"""
    sensor_members = _with_latest_sensor_data(
        project.sensor_members.filter(is_visible=True)
        .select_related("sensor", "sensor__sensor_type")
    )
    device_members = _with_latest_device_status(
        project.device_members.filter(is_visible=True)
        .select_related("device", "device__device_type")
    )
    samples = [_sensor_sample(m, project.code) for m in sensor_members]
    devices = [_device_state(m) for m in device_members]
    return {
        "project_id": project.id,
        "project_code": project.code,
        "samples": samples,
        "devices": devices,
    }


# ---------- 项目 ----------

class ProjectViewSet(_AdminWritePermission, viewsets.ModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action in ("list", "retrieve", "update", "partial_update"):
            queryset = _with_project_counts(queryset)
        return queryset

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=user)

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated])
    def layout(self, request, pk=None):
        return Response(_build_layout(self.get_object()))

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated])
    def snapshot(self, request, pk=None):
        return Response(build_project_snapshot(self.get_object()))

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated])
    def bindable_sources(self, request, pk=None):
        project = self.get_object()
        sensors_qs = Sensor.objects.select_related("sensor_type").order_by("sort_order", "sensor_id")
        devices_qs = Device.objects.select_related("device_type").order_by("sort_order", "device_id")
        section_id = request.query_params.get("section")

        sensor_members = ProjectSensorMember.objects.filter(project_id=project.id)
        device_members = ProjectDeviceMember.objects.filter(project_id=project.id)
        if section_id:
            sensor_members = sensor_members.filter(section_id=section_id)
            device_members = device_members.filter(section_id=section_id)

        bound_data_keys_by_sensor = defaultdict(list)
        for sensor_id, data_key in sensor_members.order_by(
            "sort_order", "id"
        ).values_list("sensor_id", "data_key"):
            bound_data_keys_by_sensor[sensor_id].append(data_key)
        bound_device_ids = set(
            device_members.values_list("device_id", flat=True)
        )

        ctx = {
            "project_id": project.id,
            "bound_data_keys_by_sensor": bound_data_keys_by_sensor,
            "bound_device_ids": bound_device_ids,
        }
        if section_id:
            ctx["section_id"] = section_id
        return Response({
            "sensors": BindableSensorSerializer(sensors_qs, many=True, context=ctx).data,
            "devices": BindableDeviceSerializer(devices_qs, many=True, context=ctx).data,
        })

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated])
    def series(self, request, pk=None):
        """某数据源在时间窗内的时序数据（供 timeseries 视图用，逻辑同 data_viz）。
        Query: kind=sensor|device, source_id, start, end, limit"""
        project = self.get_object()
        kind = (request.GET.get("kind") or "").lower()
        source_id = request.GET.get("source_id") or ""
        if kind not in ("sensor", "device") or not source_id:
            return Response({"detail": "kind 必须为 sensor 或 device，且 source_id 必填"}, status=400)

        now = timezone.now()
        try:
            end = _parse_dt(request.GET.get("end"), now, "end")
            start = _parse_dt(
                request.GET.get("start"), end - DEFAULT_SERIES_WINDOW, "start"
            )
            limit = _parse_series_limit(request.GET.get("limit"))
            window = end - start
        except (ValueError, OverflowError) as exc:
            return Response(
                {"detail": str(exc) or "时间参数超出支持范围"}, status=400
            )
        if window <= timedelta(0):
            return Response({"detail": "start 必须早于 end"}, status=400)
        if window > MAX_SERIES_WINDOW:
            return Response({"detail": "时间范围不能超过 31 天"}, status=400)

        if kind == "sensor":
            try:
                sensor = Sensor.objects.select_related("sensor_type").filter(
                    project_members__project=project,
                ).distinct().get(sensor_id=source_id)
            except Sensor.DoesNotExist:
                return Response(
                    {"detail": f"传感器 {source_id} 不存在或未加入该项目"},
                    status=404,
                )
            rows, total = _truncate_window(
                SensorData.objects.filter(sensor=sensor, timestamp__gte=start, timestamp__lte=end), limit,
            )
            points = [{"t": r.timestamp.isoformat(), "data": r.data} for r in rows]
            events = [
                {"t": e.timestamp.isoformat(), "event": e.event_name, "data": e.data}
                for e in SensorStatusCollection.objects.filter(
                    sensor=sensor, timestamp__gte=start, timestamp__lte=end,
                ).order_by("timestamp", "pk")[:limit]
            ]
            return Response({
                "kind": "sensor", "source_id": sensor.sensor_id, "name": sensor.name,
                "type": sensor.sensor_type.name if sensor.sensor_type_id else "",
                "fields": sensor.sensor_type.data_fields if sensor.sensor_type_id else [],
                "start": start.isoformat(), "end": end.isoformat(),
                "points": points, "count": total, "truncated": total > limit, "events": events,
            })

        try:
            device = Device.objects.select_related("device_type").filter(
                project_members__project=project,
            ).distinct().get(device_id=source_id)
        except Device.DoesNotExist:
            return Response(
                {"detail": f"设备 {source_id} 不存在或未加入该项目"},
                status=404,
            )
        rows, total = _truncate_window(
            DeviceStatusCollection.objects.filter(device=device, timestamp__gte=start, timestamp__lte=end), limit,
        )
        points = [{"t": r.timestamp.isoformat(), "data": r.data, "event": r.event_name} for r in rows]
        events = [
            {"t": r.timestamp.isoformat(), "event": r.event_name, "data": r.data}
            for r in rows if r.event_name
        ]
        return Response({
            "kind": "device", "source_id": device.device_id, "name": device.name,
            "type": device.device_type.name if device.device_type_id else "",
            "fields": device.device_type.config_parameters if device.device_type_id else [],
            "start": start.isoformat(), "end": end.isoformat(),
            "points": points, "count": total, "truncated": total > limit, "events": events,
        })


# ---------- 子资源（平铺 + ?project= 过滤） ----------

class _ProjectScopedViewSet(_AdminWritePermission, viewsets.ModelViewSet):
    """按 ?project=<id> 过滤的子资源基类。section_scoped=True 的子类额外支持 ?section=<id>。"""

    section_scoped = False

    def get_queryset(self):
        qs = super().get_queryset()
        pid = self.request.query_params.get("project")
        if pid:
            qs = qs.filter(project_id=pid)
        if self.section_scoped:
            sid = self.request.query_params.get("section")
            if sid:
                qs = qs.filter(section_id=sid)
        return qs

    def _resolve_section(self, section_id) -> ProjectSection:
        """批量导入用：取出目标房间并校验存在；缺失/无效抛 400。"""
        if not section_id:
            raise ValidationError({"section": "批量导入需指定 section（房间）"})
        try:
            return ProjectSection.objects.select_related("project").get(id=section_id)
        except ProjectSection.DoesNotExist:
            raise ValidationError({"section": "房间不存在"})


class _ControlSchemeProtectedDestroyMixin:
    """把控制方案的 PROTECT 约束转换为前端可处理的 409 响应。"""

    resource_type = "resource"
    resource_label = "资源"

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        schemes = instance.control_schemes.select_related("section").order_by("id")
        blockers = [
            {
                "id": scheme.id,
                "name": scheme.name,
                "control_type": scheme.control_type,
                "is_enabled": scheme.is_enabled,
                "status": scheme.status,
                "section_id": scheme.section_id,
                "section_name": scheme.section.name,
            }
            for scheme in schemes
        ]
        if blockers:
            return Response(
                {
                    "code": "control_scheme_in_use",
                    "detail": (
                        f"该{self.resource_label}仍被控制方案引用，请先删除相关控制方案，"
                        f"或修改方案的{self.resource_label}绑定。"
                    ),
                    "resource_type": self.resource_type,
                    "member_id": instance.pk,
                    "blockers": blockers,
                },
                status=status.HTTP_409_CONFLICT,
            )
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            # 兜底并发写入或未来新增的 PROTECT 引用，避免再暴露 500 堆栈。
            return Response(
                {
                    "code": "resource_in_use",
                    "detail": f"该{self.resource_label}正在被其他配置引用，暂时不能删除。",
                    "resource_type": self.resource_type,
                    "member_id": instance.pk,
                    "blockers": [],
                },
                status=status.HTTP_409_CONFLICT,
            )


class ProjectSectionViewSet(_ProjectScopedViewSet):
    queryset = ProjectSection.objects.all()
    serializer_class = ProjectSectionSerializer

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsAdminUser])
    def reorder(self, request):
        """按传入的 section id 顺序批量写 sort_order。body: {"order": [id, id, ...]}"""
        order = request.data.get("order", [])
        if not isinstance(order, list):
            return Response({"detail": "order 必须是 id 列表"}, status=status.HTTP_400_BAD_REQUEST)
        for idx, sec_id in enumerate(order):
            ProjectSection.objects.filter(id=sec_id).update(sort_order=idx)
        return Response({"ok": True})


class ProjectSensorMemberViewSet(_ControlSchemeProtectedDestroyMixin, _ProjectScopedViewSet):
    queryset = ProjectSensorMember.objects.select_related("sensor", "sensor__sensor_type", "section").all()
    serializer_class = ProjectSensorMemberSerializer
    section_scoped = True
    resource_type = "sensor"
    resource_label = "传感器成员"

    def create(self, request, *args, **kwargs):
        """单条或批量导入到某房间(section)：
        - 传 section + sensor_ids=[...] 走批量，按各 sensor 的 sensor_type.data_fields 拆分：
          多字段 → 每字段一条（data_key=field、tag=sensorId-field）；单字段/无元数据 → 一条 data_key=""。
          (section, sensor, data_key) 已存在的跳过；UniqueConstraint + ignore_conflicts 兜底并发。
        - 否则走标准 create（用于「+字段」单条新增，serializer 由 section 回填 project）。
        """
        sensor_ids = request.data.get("sensor_ids")
        section_id = request.data.get("section")
        if isinstance(sensor_ids, list):
            section = self._resolve_section(section_id)
            sensors = Sensor.objects.filter(id__in=sensor_ids).select_related("sensor_type")
            to_create = []
            for s in sensors:
                fields = s.sensor_type.data_fields if s.sensor_type_id else None
                fields_to_use = list(fields) if (isinstance(fields, list) and fields) else [""]
                existing = set(
                    ProjectSensorMember.objects.filter(section_id=section.id, sensor=s)
                    .values_list("data_key", flat=True)
                )
                for f in fields_to_use:
                    if f in existing:
                        continue
                    tag = (f"{s.sensor_id}-{f}" if f else s.sensor_id)[:50]
                    to_create.append(ProjectSensorMember(
                        project_id=section.project_id, section_id=section.id, sensor=s, data_key=f, tag=tag,
                    ))

            ProjectSensorMember.objects.bulk_create(to_create, ignore_conflicts=True)
            created_keys = {(b.sensor_id, b.data_key) for b in to_create}
            if created_keys:
                created_qs = ProjectSensorMember.objects.filter(
                    section_id=section.id, sensor_id__in={k[0] for k in created_keys}
                ).select_related("sensor", "sensor__sensor_type")
                created_qs = [b for b in created_qs if (b.sensor_id, b.data_key) in created_keys]
            else:
                created_qs = []
            data = ProjectSensorMemberSerializer(created_qs, many=True).data
            return Response({"created": data}, status=status.HTTP_201_CREATED)
        return super().create(request, *args, **kwargs)


class ProjectDeviceMemberViewSet(_ControlSchemeProtectedDestroyMixin, _ProjectScopedViewSet):
    queryset = ProjectDeviceMember.objects.select_related("device", "device__device_type", "section").all()
    serializer_class = ProjectDeviceMemberSerializer
    section_scoped = True
    resource_type = "device"
    resource_label = "设备成员"

    def create(self, request, *args, **kwargs):
        device_ids = request.data.get("device_ids")
        section_id = request.data.get("section")
        if isinstance(device_ids, list):
            section = self._resolve_section(section_id)
            existing = set(
                ProjectDeviceMember.objects.filter(section_id=section.id, device_id__in=device_ids)
                .values_list("device_id", flat=True)
            )
            created = []
            for d in Device.objects.filter(id__in=device_ids):
                if d.id in existing:
                    continue
                b = ProjectDeviceMember.objects.create(
                    project_id=section.project_id, section_id=section.id, device=d, tag=d.device_id,
                )
                created.append(b)
            data = ProjectDeviceMemberSerializer(created, many=True).data
            return Response({"created": data, "skipped": len(existing)}, status=status.HTTP_201_CREATED)
        return super().create(request, *args, **kwargs)


class ProjectViewViewSet(_ProjectScopedViewSet):
    queryset = ProjectView.objects.all()
    serializer_class = ProjectViewSerializer
    section_scoped = True
