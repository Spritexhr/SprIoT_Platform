from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Count, Subquery, OuterRef, IntegerField, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from datetime import timedelta
from .models import SensorType, Sensor, SensorData, SensorStatusCollection
from .serializers import (
    SensorTypeSerializer,
    SensorListSerializer,
    SensorDetailSerializer,
    SensorCreateUpdateSerializer,
    SensorDataSerializer,
    SensorStatusSerializer,
)
from resource_folders.models import ResourceFolder
from resource_folders.pagination import ResourcePageNumberPagination
from services.request_validation import parse_boolean


DEFAULT_HISTORY_HOURS = 1
MAX_HISTORY_HOURS = 24 * 31
DEFAULT_DATA_LIMIT = 200
DEFAULT_STATUS_LIMIT = 50
MAX_HISTORY_LIMIT = 2000


def _bounded_query_int(request, name, default, maximum):
    """严格解析正整数查询参数，并阻止无界历史查询。"""
    raw = request.query_params.get(name)
    if raw is None:
        return default
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
        raise ValidationError({name: f'{name} 必须是 1 到 {maximum} 之间的整数'})
    value = int(raw)
    if value < 1 or value > maximum:
        raise ValidationError({name: f'{name} 必须是 1 到 {maximum} 之间的整数'})
    return value


def _with_latest_data(queryset):
    """按服务器接收顺序附加最新数据，避免未来设备时间永久霸占当前值。"""
    latest = SensorData.objects.filter(sensor_id=OuterRef('pk')).order_by(
        '-received_at', '-pk'
    )
    return queryset.annotate(
        _latest_data=Subquery(latest.values('data')[:1]),
        _latest_timestamp=Subquery(latest.values('timestamp')[:1]),
    )


def _with_data_count_24h(queryset):
    start = timezone.now() - timedelta(hours=24)
    counts = (
        SensorData.objects.filter(sensor_id=OuterRef('pk'), received_at__gte=start)
        .order_by()
        .values('sensor_id')
        .annotate(total=Count('pk'))
        .values('total')
    )
    return queryset.annotate(
        _data_count_24h=Coalesce(
            Subquery(counts[:1], output_field=IntegerField()),
            Value(0),
        )
    )


class SensorTypeViewSet(viewsets.ModelViewSet):
    """传感器类型 CRUD API，创建/修改/删除仅限工作人员（is_staff）"""
    queryset = SensorType.objects.annotate(_sensor_count=Count('sensors')).order_by('name')
    serializer_class = SensorTypeSerializer

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsAuthenticated(), IsAdminUser()]
        return [IsAuthenticated()]


class SensorViewSet(viewsets.ModelViewSet):
    """
    传感器 CRUD API
    支持按 sensor_id 查找、筛选、搜索
    创建/修改/删除/发送命令仅限工作人员，非工作人员仅可查看
    """
    queryset = Sensor.objects.select_related('sensor_type', 'folder').all()
    lookup_field = 'sensor_id'
    pagination_class = ResourcePageNumberPagination

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy', 'send_command', 'bulk_move'):
            return [IsAuthenticated(), IsAdminUser()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return SensorDetailSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return SensorCreateUpdateSerializer
        return SensorListSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        # 按类型筛选
        sensor_type_id = self.request.query_params.get('sensor_type')
        if sensor_type_id:
            qs = qs.filter(sensor_type_id=sensor_type_id)
        # 按在线状态筛选（基于 last_seen 实时计算，3分钟内有数据视为在线）
        online = self.request.query_params.get('online')
        if online is not None:
            now = timezone.now()
            threshold = now - timedelta(minutes=3)
            if online == 'true':
                qs = qs.filter(last_seen__gt=threshold, last_seen__lte=now)
            elif online == 'false':
                qs = qs.filter(
                    Q(last_seen__isnull=True)
                    | Q(last_seen__lte=threshold)
                    | Q(last_seen__gt=now)
                )
        # 搜索
        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(sensor_id__icontains=search))
        folder = self.request.query_params.get('folder')
        if folder == 'unfiled':
            qs = qs.filter(folder__isnull=True)
        elif folder:
            if not folder.isdigit():
                return qs.none()
            qs = qs.filter(folder_id=int(folder), folder__resource_type=ResourceFolder.SENSOR)
        action_name = getattr(self, 'action', None)
        if action_name in ('list', 'retrieve'):
            qs = _with_latest_data(qs)
        if action_name == 'retrieve':
            qs = _with_data_count_24h(qs)
        return qs

    @action(detail=False, methods=['post'], url_path='bulk-move')
    def bulk_move(self, request):
        sensor_ids = request.data.get('sensor_ids')
        folder_id = request.data.get('folder')
        if not isinstance(sensor_ids, list) or not sensor_ids or not all(isinstance(x, str) for x in sensor_ids):
            return Response({'detail': 'sensor_ids 必须是非空字符串数组'}, status=400)
        folder = None
        if folder_id is not None:
            try:
                folder = ResourceFolder.objects.get(pk=folder_id, resource_type=ResourceFolder.SENSOR)
            except ResourceFolder.DoesNotExist:
                return Response({'detail': '传感器文件夹不存在'}, status=400)
        unique_ids = set(sensor_ids)
        qs = Sensor.objects.filter(sensor_id__in=unique_ids)
        if qs.count() != len(unique_ids):
            return Response({'detail': '包含不存在的传感器'}, status=400)
        updated = qs.update(folder=folder)
        return Response({'updated': updated})

    @action(detail=True, methods=['get'], url_path='data')
    def sensor_data(self, request, sensor_id=None):
        """获取传感器历史数据，支持时间范围查询"""
        hours = _bounded_query_int(
            request, 'hours', DEFAULT_HISTORY_HOURS, MAX_HISTORY_HOURS
        )
        limit = _bounded_query_int(
            request, 'limit', DEFAULT_DATA_LIMIT, MAX_HISTORY_LIMIT
        )
        sensor = self.get_object()
        now = timezone.now()
        start_time = now - timedelta(hours=hours)
        records = SensorData.objects.filter(
            sensor=sensor,
            timestamp__gte=start_time,
            timestamp__lte=now,
        ).order_by('-timestamp', '-pk')[:limit]
        serializer = SensorDataSerializer(records, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='status')
    def sensor_status(self, request, sensor_id=None):
        """获取传感器状态记录"""
        limit = _bounded_query_int(
            request, 'limit', DEFAULT_STATUS_LIMIT, MAX_HISTORY_LIMIT
        )
        sensor = self.get_object()
        records = SensorStatusCollection.objects.filter(
            sensor=sensor,
            timestamp__lte=timezone.now(),
        ).order_by('-timestamp', '-pk')[:limit]
        serializer = SensorStatusSerializer(records, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='reorder',
            permission_classes=[IsAuthenticated, IsAdminUser])
    def reorder(self, request):
        """
        批量更新传感器显示顺序。
        请求体：{"order": ["sensor_id_a", "sensor_id_b", ...]}
        按数组顺序写入 sort_order = 1, 2, ...，未列出的传感器保持原值。
        """
        order = request.data.get('order')
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            return Response(
                {'detail': 'order 必须是 sensor_id 字符串数组'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        page = request.data.get('page')
        page_size = request.data.get('page_size')
        folder = request.data.get('folder')
        if page is not None or page_size is not None:
            try:
                page = int(page)
                page_size = int(page_size)
                if page < 1 or page_size < 1 or page_size > 96:
                    raise ValueError
            except (TypeError, ValueError):
                return Response({'detail': 'page/page_size 参数无效'}, status=400)

            scope = Sensor.objects.all()
            if folder == 'unfiled' or folder is None:
                scope = scope.filter(folder__isnull=True)
            else:
                scope = scope.filter(folder_id=folder)
            all_ids = list(
                scope.order_by('sort_order', '-created_at').values_list('sensor_id', flat=True)
            )
            start = (page - 1) * page_size
            page_ids = all_ids[start:start + page_size]
            if len(order) != len(page_ids) or set(order) != set(page_ids):
                return Response({'detail': '排序内容与当前页资源不一致，请刷新后重试'}, status=409)
            all_ids[start:start + len(page_ids)] = order
            rows = list(Sensor.objects.filter(sensor_id__in=all_ids))
            row_map = {row.sensor_id: row for row in rows}
            for index, sensor_id in enumerate(all_ids, start=1):
                row_map[sensor_id].sort_order = index
            with transaction.atomic():
                Sensor.objects.bulk_update(rows, ['sort_order'])
        else:
            # 兼容旧客户端：仅更新请求中给出的顺序。
            with transaction.atomic():
                for index, sensor_id in enumerate(order, start=1):
                    Sensor.objects.filter(sensor_id=sensor_id).update(sort_order=index)
        return Response({'updated': len(order)})

    @action(detail=True, methods=['post'], url_path='command')
    def send_command(self, request, sensor_id=None):
        """向传感器发送命令"""
        sensor = self.get_object()
        command_name = request.data.get('command_name')
        params = request.data.get('params', {})
        try:
            make_sure = parse_boolean(
                request.data.get('make_sure'),
                field_name='make_sure',
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if not isinstance(command_name, str) or not command_name.strip():
            return Response(
                {'detail': 'command_name 必须是非空字符串'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        command_name = command_name.strip()
        if not isinstance(params, dict):
            return Response(
                {'detail': 'params 必须是 JSON 对象'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from services.sensors_service.sensor_command_send_service import sensor_command_send_service
            if make_sure:
                result = sensor_command_send_service.send_command_with_make_sure(
                    sensor.sensor_id, command_name, params or None
                )
            else:
                result = sensor_command_send_service.send_command(
                    sensor.sensor_id, command_name, params or None
                )
            return Response({'success': result, 'command': command_name})
        except Exception as e:
            return Response(
                {'detail': str(e), 'success': False},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
