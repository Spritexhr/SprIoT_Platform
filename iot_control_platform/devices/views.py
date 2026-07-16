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
from .models import DeviceType, Device, DeviceStatusCollection
from .online_status import get_device_offline_timeout
from .serializers import (
    DeviceTypeSerializer,
    DeviceListSerializer,
    DeviceDetailSerializer,
    DeviceCreateUpdateSerializer,
    DeviceStatusSerializer,
)
from resource_folders.models import ResourceFolder
from resource_folders.pagination import ResourcePageNumberPagination
from services.request_validation import parse_boolean


DEFAULT_HISTORY_HOURS = 1
MAX_HISTORY_HOURS = 24 * 31
DEFAULT_HISTORY_LIMIT = 200
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


def _with_latest_status(queryset):
    """按服务器接收顺序附加最新状态，查询数不随设备数量增长。"""
    latest = DeviceStatusCollection.objects.filter(
        device_id=OuterRef('pk')
    ).order_by('-received_at', '-pk')
    return queryset.annotate(
        _latest_status_data=Subquery(latest.values('data')[:1]),
        _latest_status_event_name=Subquery(latest.values('event_name')[:1]),
        _latest_status_timestamp=Subquery(latest.values('timestamp')[:1]),
    )


def _with_data_count_24h(queryset):
    start = timezone.now() - timedelta(hours=24)
    counts = (
        DeviceStatusCollection.objects.filter(
            device_id=OuterRef('pk'), received_at__gte=start
        )
        .order_by()
        .values('device_id')
        .annotate(total=Count('pk'))
        .values('total')
    )
    return queryset.annotate(
        _data_count_24h=Coalesce(
            Subquery(counts[:1], output_field=IntegerField()),
            Value(0),
        )
    )


class DeviceTypeViewSet(viewsets.ModelViewSet):
    """设备类型 CRUD API，创建/修改/删除仅限工作人员（is_staff）"""
    queryset = DeviceType.objects.annotate(_device_count=Count('devices')).order_by('name')
    serializer_class = DeviceTypeSerializer

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsAuthenticated(), IsAdminUser()]
        return [IsAuthenticated()]


class DeviceViewSet(viewsets.ModelViewSet):
    """
    设备 CRUD API
    支持按 device_id 查找、筛选、搜索
    创建/修改/删除/发送命令仅限工作人员，非工作人员仅可查看
    """
    queryset = Device.objects.select_related('device_type', 'folder').all()
    lookup_field = 'device_id'
    pagination_class = ResourcePageNumberPagination

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy', 'send_command', 'bulk_move'):
            return [IsAuthenticated(), IsAdminUser()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return DeviceDetailSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return DeviceCreateUpdateSerializer
        return DeviceListSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        # 按类型筛选
        device_type_id = self.request.query_params.get('device_type')
        if device_type_id:
            qs = qs.filter(device_type_id=device_type_id)
        # 按在线状态筛选（基于 last_seen 动态计算）
        online = self.request.query_params.get('online')
        if online is not None:
            now = timezone.now()
            threshold = now - timedelta(
                seconds=get_device_offline_timeout()
            )
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
            qs = qs.filter(Q(name__icontains=search) | Q(device_id__icontains=search))
        folder = self.request.query_params.get('folder')
        if folder == 'unfiled':
            qs = qs.filter(folder__isnull=True)
        elif folder:
            if not folder.isdigit():
                return qs.none()
            qs = qs.filter(folder_id=int(folder), folder__resource_type=ResourceFolder.DEVICE)
        action_name = getattr(self, 'action', None)
        if action_name in ('list', 'retrieve'):
            qs = _with_latest_status(qs)
        if action_name == 'retrieve':
            qs = _with_data_count_24h(qs)
        return qs

    @action(detail=False, methods=['post'], url_path='bulk-move')
    def bulk_move(self, request):
        device_ids = request.data.get('device_ids')
        folder_id = request.data.get('folder')
        if not isinstance(device_ids, list) or not device_ids or not all(isinstance(x, str) for x in device_ids):
            return Response({'detail': 'device_ids 必须是非空字符串数组'}, status=400)
        folder = None
        if folder_id is not None:
            try:
                folder = ResourceFolder.objects.get(pk=folder_id, resource_type=ResourceFolder.DEVICE)
            except ResourceFolder.DoesNotExist:
                return Response({'detail': '设备文件夹不存在'}, status=400)
        unique_ids = set(device_ids)
        qs = Device.objects.filter(device_id__in=unique_ids)
        if qs.count() != len(unique_ids):
            return Response({'detail': '包含不存在的设备'}, status=400)
        updated = qs.update(folder=folder)
        return Response({'updated': updated})

    @action(detail=True, methods=['get'], url_path='status')
    def device_status(self, request, device_id=None):
        """获取设备历史状态记录，支持时间范围查询。"""
        hours = _bounded_query_int(
            request, 'hours', DEFAULT_HISTORY_HOURS, MAX_HISTORY_HOURS
        )
        limit = _bounded_query_int(
            request, 'limit', DEFAULT_HISTORY_LIMIT, MAX_HISTORY_LIMIT
        )
        device = self.get_object()
        now = timezone.now()
        start_time = now - timedelta(hours=hours)
        records = DeviceStatusCollection.objects.filter(
            device=device,
            timestamp__gte=start_time,
            timestamp__lte=now,
        ).order_by('-timestamp', '-pk')[:limit]
        serializer = DeviceStatusSerializer(records, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='reorder',
            permission_classes=[IsAuthenticated, IsAdminUser])
    def reorder(self, request):
        """
        批量更新设备显示顺序。
        请求体：{"order": ["device_id_a", "device_id_b", ...]}
        按数组顺序写入 sort_order = 1, 2, ...，未列出的设备保持原值。
        """
        order = request.data.get('order')
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            return Response(
                {'detail': 'order 必须是 device_id 字符串数组'},
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

            scope = Device.objects.all()
            if folder == 'unfiled' or folder is None:
                scope = scope.filter(folder__isnull=True)
            else:
                scope = scope.filter(folder_id=folder)
            all_ids = list(
                scope.order_by('sort_order', '-created_at').values_list('device_id', flat=True)
            )
            start = (page - 1) * page_size
            page_ids = all_ids[start:start + page_size]
            if len(order) != len(page_ids) or set(order) != set(page_ids):
                return Response({'detail': '排序内容与当前页资源不一致，请刷新后重试'}, status=409)
            all_ids[start:start + len(page_ids)] = order
            rows = list(Device.objects.filter(device_id__in=all_ids))
            row_map = {row.device_id: row for row in rows}
            for index, device_id in enumerate(all_ids, start=1):
                row_map[device_id].sort_order = index
            with transaction.atomic():
                Device.objects.bulk_update(rows, ['sort_order'])
        else:
            # 兼容旧客户端：仅更新请求中给出的顺序。
            with transaction.atomic():
                for index, device_id in enumerate(order, start=1):
                    Device.objects.filter(device_id=device_id).update(sort_order=index)
        return Response({'updated': len(order)})

    @action(detail=True, methods=['post'], url_path='command')
    def send_command(self, request, device_id=None):
        """向设备发送命令"""
        device = self.get_object()
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
            from services.devices_service.device_command_send_service import device_command_send_service
            if make_sure:
                result = device_command_send_service.send_command_with_make_sure(
                    device.device_id, command_name, params or None
                )
            else:
                result = device_command_send_service.send_command(
                    device.device_id, command_name, params or None
                )
            return Response({'success': result, 'command': command_name})
        except Exception as e:
            return Response(
                {'detail': str(e), 'success': False},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
