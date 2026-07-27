"""
data_viz 插件 - 时间段数据可视化

提供:
- GET /ping/                    端到端探针
- GET /sources/                 列出所有 sensor + device，附带可绘制字段
- GET /series/?kind=&source_id= 取指定来源在时间窗内的时序数据 + 状态事件
"""
from datetime import timedelta

from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from devices.models import Device, DeviceStatusCollection
from sensors.models import Sensor, SensorData, SensorStatusCollection

# 单次返回上限，避免一次性把 10w 行扔给前端图表
DEFAULT_LIMIT = 2000
MAX_LIMIT = 10000
DEFAULT_WINDOW = timedelta(hours=24)
MAX_WINDOW = timedelta(days=31)
# sources 正常响应仍保持 {"sensors": [...], "devices": [...]}，但用总数量和
# JSON 体积双重上限避免异常元数据或海量资源拖垮 worker / 浏览器。
MAX_SOURCE_COUNT = 2000
MAX_SOURCES_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SERIES_RESPONSE_BYTES = 8 * 1024 * 1024


def _parse_dt(value, default, param_name):
    """严格解析 ISO 时间；只有参数缺省时才使用默认值。"""
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


def _parse_limit(value):
    """严格解析返回上限，拒绝静默纠正无效或超限输入。"""
    if value is None:
        return DEFAULT_LIMIT
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError(f"limit 必须是 1 到 {MAX_LIMIT} 之间的整数")
    try:
        limit = int(value)
    except ValueError as exc:
        raise ValueError(f"limit 必须是 1 到 {MAX_LIMIT} 之间的整数") from exc
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit 必须是 1 到 {MAX_LIMIT} 之间的整数")
    return limit


class _ResultTooLarge(Exception):
    pass


def _json_size(payload):
    encoder = DjangoJSONEncoder(ensure_ascii=False, separators=(",", ":"))
    return sum(len(chunk.encode("utf-8")) for chunk in encoder.iterencode(payload))


def _collect_latest(qs, limit, serialize, *, byte_budget):
    """分块读取最近记录并在构造期间执行容量检查。

    不能先 ``list(qs[:limit])`` 再检查最终响应：单行 JSONField 允许较大时，
    这种顺序会在返回 413 之前就占用数百 MiB。这里每次最多从数据库取 16 行，
    一旦超过预算立即停止迭代。
    """
    rows = []
    used_bytes = 0
    latest = qs.order_by("-timestamp", "-pk")[:limit]
    for record in latest.iterator(chunk_size=16):
        item = serialize(record)
        used_bytes += _json_size(item) + 1
        if used_bytes > byte_budget:
            raise _ResultTooLarge
        rows.append(item)
    rows.reverse()
    return rows, used_bytes


def _json_exceeds_bytes(payload, max_bytes):
    """增量估算最终 JSON UTF-8 体积，避免为了检查上限再复制一份大字符串。"""
    encoder = DjangoJSONEncoder(ensure_ascii=False, separators=(",", ":"))
    size = 0
    for chunk in encoder.iterencode(payload):
        size += len(chunk.encode("utf-8"))
        if size > max_bytes:
            return True
    return False


def _bounded_response(payload, *, max_bytes, resource_label):
    """成功响应结构不变；超限时给前端一个可解释、可重试缩小范围的错误。"""
    if _json_exceeds_bytes(payload, max_bytes):
        return Response(
            {
                "detail": (
                    f"{resource_label}结果过大，请缩小时间范围、降低 limit，"
                    "或减少数据源元数据"
                ),
                "code": "result_too_large",
            },
            status=413,
        )
    return Response(payload)


def _result_too_large_response(resource_label):
    return Response(
        {
            "detail": (
                f"{resource_label}结果过大，请缩小时间范围、降低 limit，"
                "或减少数据源元数据"
            ),
            "code": "result_too_large",
        },
        status=413,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ping(request):
    """端到端验证"""
    return Response({"plugin": "data_viz", "status": "ok"})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sources(request):
    """
    列出可视化数据源
    返回所有 sensor + device 及其可绘制字段（来自类型定义）
    """
    sensor_qs = Sensor.objects.select_related("sensor_type").only(
        "sensor_id", "name", "location", "last_seen",
        "sensor_type__name", "sensor_type__data_fields",
    ).order_by("pk")
    sensor_list = []
    source_bytes = 0
    for index, s in enumerate(
        sensor_qs[:MAX_SOURCE_COUNT + 1].iterator(chunk_size=100)
    ):
        if index >= MAX_SOURCE_COUNT:
            return Response(
                {
                    "detail": (
                        f"可视化数据源超过 {MAX_SOURCE_COUNT} 个，"
                        "请使用项目视图，或由管理员归档无关数据源"
                    ),
                    "code": "result_too_large",
                },
                status=413,
            )
        item = {
            "id": s.sensor_id,
            "name": s.name,
            "type": s.sensor_type.name if s.sensor_type else "",
            "data_fields": s.sensor_type.data_fields if s.sensor_type else [],
            "is_online": s.computed_is_online,
            "last_seen": s.last_seen.isoformat() if s.last_seen else None,
            "location": s.location,
        }
        source_bytes += _json_size(item) + 1
        if source_bytes > MAX_SOURCES_RESPONSE_BYTES:
            return _result_too_large_response("数据源")
        sensor_list.append(item)

    remaining = MAX_SOURCE_COUNT - len(sensor_list)
    device_qs = Device.objects.select_related("device_type").only(
        "device_id", "name", "location", "last_seen",
        "device_type__name", "device_type__config_parameters",
    ).order_by("pk")
    device_list = []
    for index, d in enumerate(device_qs[:remaining + 1].iterator(chunk_size=100)):
        if index >= remaining:
            return Response(
                {
                    "detail": (
                        f"可视化数据源总数超过 {MAX_SOURCE_COUNT} 个，"
                        "请使用项目视图，或由管理员归档无关数据源"
                    ),
                    "code": "result_too_large",
                },
                status=413,
            )
        item = {
            "id": d.device_id,
            "name": d.name,
            "type": d.device_type.name if d.device_type else "",
            "config_parameters": d.device_type.config_parameters if d.device_type else [],
            "is_online": d.computed_is_online,
            "last_seen": d.last_seen.isoformat() if d.last_seen else None,
            "location": d.location,
        }
        source_bytes += _json_size(item) + 1
        if source_bytes > MAX_SOURCES_RESPONSE_BYTES:
            return _result_too_large_response("数据源")
        device_list.append(item)

    return _bounded_response(
        {"sensors": sensor_list, "devices": device_list},
        max_bytes=MAX_SOURCES_RESPONSE_BYTES,
        resource_label="数据源",
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def series(request):
    """
    取指定来源的时间窗时序数据
    Query:
      kind:       sensor | device
      source_id:  Sensor.sensor_id 或 Device.device_id
      start, end: ISO 时间，缺省为最近 24 小时
      limit:      返回点上限，默认 2000，最大 10000
    """
    kind = (request.GET.get("kind") or "").lower()
    source_id = request.GET.get("source_id") or ""
    if kind not in ("sensor", "device") or not source_id:
        return Response(
            {"detail": "kind 必须为 sensor 或 device，且 source_id 必填"},
            status=400,
        )

    now = timezone.now()
    try:
        end = _parse_dt(request.GET.get("end"), now, "end")
        start_raw = request.GET.get("start")
        if start_raw is None:
            start = end - DEFAULT_WINDOW
        else:
            start = _parse_dt(start_raw, None, "start")
        limit = _parse_limit(request.GET.get("limit"))
        window = end - start
    except (ValueError, OverflowError) as exc:
        return Response({"detail": str(exc) or "时间参数超出支持范围"}, status=400)

    if window <= timedelta(0):
        return Response({"detail": "start 必须早于 end"}, status=400)
    if window > MAX_WINDOW:
        return Response({"detail": "时间范围不能超过 31 天"}, status=400)

    if kind == "sensor":
        try:
            sensor = Sensor.objects.select_related("sensor_type").get(sensor_id=source_id)
        except Sensor.DoesNotExist:
            return Response({"detail": f"传感器 {source_id} 不存在"}, status=404)

        data_qs = SensorData.objects.filter(
            sensor=sensor, timestamp__gte=start, timestamp__lte=end
        )
        total = data_qs.count()

        # 状态事件：上限单独限制，不与 data 共用
        events_qs = SensorStatusCollection.objects.filter(
            sensor=sensor, timestamp__gte=start, timestamp__lte=end
        )
        try:
            points, used_bytes = _collect_latest(
                data_qs,
                limit,
                lambda row: {
                    "t": row.timestamp.isoformat(),
                    "data": row.data,
                },
                byte_budget=MAX_SERIES_RESPONSE_BYTES,
            )
            events, _event_bytes = _collect_latest(
                events_qs,
                limit,
                lambda row: {
                    "t": row.timestamp.isoformat(),
                    "event": row.event_name,
                    "data": row.data,
                },
                byte_budget=max(0, MAX_SERIES_RESPONSE_BYTES - used_bytes),
            )
        except _ResultTooLarge:
            return _result_too_large_response("时序数据")

        payload = {
            "kind": "sensor",
            "source_id": sensor.sensor_id,
            "name": sensor.name,
            "type": sensor.sensor_type.name if sensor.sensor_type else "",
            "fields": sensor.sensor_type.data_fields if sensor.sensor_type else [],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "points": points,
            "count": total,
            "truncated": total > limit,
            "events": events,
        }
        return _bounded_response(
            payload,
            max_bytes=MAX_SERIES_RESPONSE_BYTES,
            resource_label="时序数据",
        )

    # kind == "device"
    try:
        device = Device.objects.select_related("device_type").get(device_id=source_id)
    except Device.DoesNotExist:
        return Response({"detail": f"设备 {source_id} 不存在"}, status=404)

    data_qs = DeviceStatusCollection.objects.filter(
        device=device, timestamp__gte=start, timestamp__lte=end
    )
    total = data_qs.count()
    try:
        points, _used_bytes = _collect_latest(
            data_qs,
            limit,
            lambda row: {
                "t": row.timestamp.isoformat(),
                "data": row.data,
                "event": row.event_name,
            },
            byte_budget=MAX_SERIES_RESPONSE_BYTES,
        )
    except _ResultTooLarge:
        return _result_too_large_response("时序数据")
    events = [
        {"t": point["t"], "event": point["event"], "data": point["data"]}
        for point in points
        if point["event"]
    ]

    payload = {
        "kind": "device",
        "source_id": device.device_id,
        "name": device.name,
        "type": device.device_type.name if device.device_type else "",
        "fields": device.device_type.config_parameters if device.device_type else [],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "points": points,
        "count": total,
        "truncated": total > limit,
        "events": events,
    }
    return _bounded_response(
        payload,
        max_bytes=MAX_SERIES_RESPONSE_BYTES,
        resource_label="时序数据",
    )
