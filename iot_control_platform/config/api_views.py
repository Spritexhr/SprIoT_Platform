"""
全局 API 视图（不属于特定 app 的接口）
"""
import logging

from django.conf import settings
from django.db import connection
from django.db.models import Count, OuterRef, Q, Subquery
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.utils import timezone
from datetime import timedelta

from config.platform_config import get_config

logger = logging.getLogger(__name__)


def _check_database_connection() -> bool:
    """执行真实 SQL；MySQL 探针使用独立短超时连接，不影响业务查询超时。"""
    if connection.vendor == "mysql":
        params = connection.get_connection_params()
        params.update(
            connect_timeout=1,
            read_timeout=1,
            write_timeout=1,
        )
        probe = connection.Database.connect(**params)
        try:
            cursor = probe.cursor()
            try:
                cursor.execute("SELECT 1")
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            probe.close()
        return bool(row and row[0] == 1)

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        row = cursor.fetchone()
    return bool(row and row[0] == 1)


def _check_redis_connection() -> bool:
    """使用独立短超时连接 PING Redis，保证容器探针不会长时间挂住。"""
    import redis

    client = redis.Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
    )
    try:
        return bool(client.ping())
    finally:
        client.close()


@api_view(['GET'])
@permission_classes([AllowAny])
@throttle_classes([])
def backend_health_check(request):
    """仅检查 backend 自身依赖，供容器 healthcheck 使用。

    不要求 mqtt_runner 已经启动，避免 backend 与 runner 的启动健康检查互相
    等待；但会真实检查 MySQL 和 Redis，而不是只检查 TCP 端口。
    """
    checks = {}
    try:
        checks["database"] = "ok" if _check_database_connection() else "error"
    except Exception as exc:
        logger.warning("Backend 健康检查数据库失败: %s", exc)
        checks["database"] = "error"

    try:
        checks["redis"] = "ok" if _check_redis_connection() else "error"
    except Exception as exc:
        logger.warning("Backend 健康检查 Redis 失败: %s", exc)
        checks["redis"] = "error"

    healthy = all(value == "ok" for value in checks.values())
    return Response(
        {
            "status": "ok" if healthy else "degraded",
            "checks": checks,
            "timestamp": timezone.now().isoformat(),
        },
        status=200 if healthy else 503,
    )


@api_view(['GET'])
@permission_classes([AllowAny])
@throttle_classes([])
def health_check(request):
    """
    健康检查端点
    返回服务状态、MQTT 连接状态和数据库连接状态
    无需认证，供监控系统和负载均衡器使用
    """
    checks = {}

    # 数据库检查
    try:
        if not _check_database_connection():
            raise RuntimeError("database health query returned unexpected result")
        checks['database'] = 'ok'
    except Exception as e:
        logger.warning("健康检查数据库失败: %s", e)
        checks['database'] = 'error'

    # MQTT 检查
    try:
        from services.mqtt_command_bus import get_mqtt_command_bus
        runner = get_mqtt_command_bus().get_runner_status()
        checks['mqtt'] = (
            'connected'
            if runner.get('is_connected') == '1'
            and runner.get('command_worker_alive') == '1'
            else 'disconnected'
        )
    except Exception as e:
        logger.warning("健康检查 MQTT runner 失败: %s", e)
        checks['mqtt'] = 'error'

    overall = 'ok' if all(v in ('ok', 'connected') for v in checks.values()) else 'degraded'
    status_code = 200 if overall == 'ok' else 503

    return Response({
        'status': overall,
        'checks': checks,
        'timestamp': timezone.now().isoformat(),
    }, status=status_code)


@api_view(['GET'])
def mqtt_status(request):
    """获取 MQTT 连接状态（从 platform_config 读取当前配置）"""
    try:
        from services.mqtt_command_bus import get_mqtt_command_bus
        runner = get_mqtt_command_bus().get_runner_status()
        is_connected = runner.get('is_connected') == '1'
        command_worker_alive = runner.get('command_worker_alive') == '1'
        runner_state = runner.get('state', 'unavailable')
        last_error = runner.get('last_error', '')
    except Exception:
        is_connected = False
        command_worker_alive = False
        runner_state = 'unavailable'
        last_error = ''

    return Response({
        'broker': get_config("mqtt_broker", "127.0.0.1", str),
        'port': get_config("mqtt_port", 1883, int),
        'is_connected': is_connected,
        'command_worker_alive': command_worker_alive,
        'runner_state': runner_state,
        'last_error': last_error,
    })


@api_view(['GET'])
def dashboard_stats(request):
    """仪表盘统计数据"""
    from sensors.models import Sensor, SensorData
    from devices.models import Device, DeviceStatusCollection
    from automation.models import AutomationRule

    now = timezone.now()
    sensor_online_threshold = now - timedelta(minutes=3)
    from devices.online_status import get_device_offline_timeout
    device_online_timeout = get_device_offline_timeout()
    device_online_threshold = now - timedelta(seconds=device_online_timeout)
    last_24h = now - timedelta(hours=24)

    # 传感器统计
    sensor_stats = Sensor.objects.aggregate(
        total=Count('pk'),
        online=Count(
            'pk',
            filter=Q(
                last_seen__gt=sensor_online_threshold,
                last_seen__lte=now,
            ),
        ),
    )
    sensor_total = sensor_stats['total']
    sensor_online = sensor_stats['online']

    # 设备统计
    device_stats = Device.objects.aggregate(
        total=Count('pk'),
        online=Count(
            'pk',
            filter=Q(
                last_seen__gt=device_online_threshold,
                last_seen__lte=now,
            ),
        ),
    )
    device_total = device_stats['total']
    device_online = device_stats['online']

    # 自动化规则统计
    rule_total = AutomationRule.objects.count()

    # 24小时数据量
    sensor_data_24h = SensorData.objects.filter(received_at__gte=last_24h).count()
    device_data_24h = DeviceStatusCollection.objects.filter(received_at__gte=last_24h).count()

    # 最近传感器数据（每个传感器最新一条）
    latest_sensor_data = SensorData.objects.filter(
        sensor_id=OuterRef('pk')
    ).order_by('-received_at', '-pk')
    recent_sensors = []
    recent_sensor_qs = Sensor.objects.select_related('sensor_type').annotate(
        _dashboard_latest_data=Subquery(latest_sensor_data.values('data')[:1]),
        _dashboard_latest_time=Subquery(latest_sensor_data.values('timestamp')[:1]),
    )[:20]
    for s in recent_sensor_qs:
        is_online = bool(
            s.last_seen
            and timedelta(0) <= (now - s.last_seen) < timedelta(minutes=3)
        )
        recent_sensors.append({
            'sensor_id': s.sensor_id,
            'name': s.name,
            'type_name': s.sensor_type.name if s.sensor_type else '--',
            'is_online': is_online,
            'last_seen': s.last_seen,
            'latest_data': s._dashboard_latest_data,
            'latest_time': s._dashboard_latest_time,
        })

    # 最近设备状态
    latest_device_status = DeviceStatusCollection.objects.filter(
        device_id=OuterRef('pk')
    ).order_by('-received_at', '-pk')
    recent_devices = []
    recent_device_qs = Device.objects.select_related('device_type').annotate(
        _dashboard_latest_data=Subquery(latest_device_status.values('data')[:1]),
        _dashboard_latest_time=Subquery(latest_device_status.values('timestamp')[:1]),
    )[:20]
    for d in recent_device_qs:
        is_online = bool(
            d.last_seen
            and 0
            <= (now - d.last_seen).total_seconds()
            < device_online_timeout
        )
        recent_devices.append({
            'device_id': d.device_id,
            'name': d.name,
            'type_name': d.device_type.name if d.device_type else '--',
            'is_online': is_online,
            'last_seen': d.last_seen,
            'latest_data': d._dashboard_latest_data,
            'latest_time': d._dashboard_latest_time,
        })

    # 自动化规则列表
    recent_rules = list(
        AutomationRule.objects.values('id', 'name', 'script_id', 'updated_at')
        .order_by('-updated_at')[:10]
    )

    return Response({
        'sensor_total': sensor_total,
        'sensor_online': sensor_online,
        'device_total': device_total,
        'device_online': device_online,
        'rule_total': rule_total,
        'sensor_data_24h': sensor_data_24h,
        'device_data_24h': device_data_24h,
        'recent_sensors': recent_sensors,
        'recent_devices': recent_devices,
        'recent_rules': recent_rules,
    })
