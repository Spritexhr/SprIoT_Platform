from datetime import timedelta

from rest_framework import serializers
from django.utils import timezone
from .models import DeviceType, Device, DeviceStatusCollection
from .online_status import get_device_offline_timeout
from sensors.serializers import normalize_commands_payload


class DeviceTypeSerializer(serializers.ModelSerializer):
    """设备类型序列化器"""
    device_count = serializers.SerializerMethodField()

    class Meta:
        model = DeviceType
        fields = [
            'id', 'DeviceType_id', 'name', 'description',
            'config_parameters', 'commands',
            'created_at', 'device_count',
        ]
        read_only_fields = ['id', 'created_at', 'device_count']

    def validate_commands(self, value):
        return normalize_commands_payload(value)

    def get_device_count(self, obj):
        # 优先使用 annotate 预计算的值
        if hasattr(obj, '_device_count'):
            return obj._device_count
        return obj.devices.count()


class DeviceTypeBriefSerializer(serializers.ModelSerializer):
    """设备类型简要序列化器（用于嵌套）"""
    class Meta:
        model = DeviceType
        fields = ['id', 'DeviceType_id', 'name', 'config_parameters', 'commands']


class DeviceListSerializer(serializers.ModelSerializer):
    """设备列表序列化器（含类型信息和最新状态）"""
    device_type_info = DeviceTypeBriefSerializer(source='device_type', read_only=True)
    latest_data = serializers.SerializerMethodField()
    is_online = serializers.SerializerMethodField()
    folder_info = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = [
            'id', 'device_id', 'name', 'description', 'location',
            'mqtt_topic_data', 'mqtt_topic_control',
            'is_online', 'last_seen',
            'sort_order',
            'created_at', 'updated_at',
            'device_type', 'device_type_info', 'latest_data', 'folder', 'folder_info',
        ]

    def get_folder_info(self, obj):
        if not obj.folder_id:
            return None
        return {'id': obj.folder_id, 'name': obj.folder.name, 'parent': obj.folder.parent_id}

    def get_is_online(self, obj):
        """使用平台统一的 device_offline_timeout 判定在线状态。"""
        return bool(obj.computed_is_online)

    def get_latest_data(self, obj):
        # API 列表/详情通过相关子查询注入，查询数不随资源数量增长。
        if hasattr(obj, '_latest_status_timestamp'):
            if obj._latest_status_timestamp is None:
                return None
            return {
                'data': obj._latest_status_data,
                'event_name': obj._latest_status_event_name,
                'timestamp': obj._latest_status_timestamp,
            }
        # 兼容其它调用方显式提供的预取结果。
        if hasattr(obj, '_prefetched_objects_cache') and 'status_records' in obj._prefetched_objects_cache:
            records = obj._prefetched_objects_cache['status_records']
            if records:
                return {
                    'data': records[0].data,
                    'event_name': records[0].event_name,
                    'timestamp': records[0].timestamp,
                }
            return None
        # 回退到查询
        record = obj.status_records.order_by('-received_at', '-pk').first()
        if record:
            return {
                'data': record.data,
                'event_name': record.event_name,
                'timestamp': record.timestamp,
            }
        return None


class DeviceDetailSerializer(DeviceListSerializer):
    """设备详情序列化器"""
    data_count_24h = serializers.SerializerMethodField()

    class Meta(DeviceListSerializer.Meta):
        fields = DeviceListSerializer.Meta.fields + ['data_count_24h']

    def get_data_count_24h(self, obj):
        # 优先使用 annotate 预计算的值
        if hasattr(obj, '_data_count_24h'):
            return obj._data_count_24h
        start = timezone.now() - timedelta(hours=24)
        return obj.status_records.filter(received_at__gte=start).count()


class DeviceCreateUpdateSerializer(serializers.ModelSerializer):
    """设备创建/更新序列化器"""
    class Meta:
        model = Device
        fields = [
            'device_id', 'name', 'description', 'location', 'device_type', 'folder',
        ]

    def validate_folder(self, value):
        if value is not None and value.resource_type != 'device':
            raise serializers.ValidationError('请选择设备文件夹')
        return value


class DeviceStatusSerializer(serializers.ModelSerializer):
    """设备状态记录序列化器（与 SensorStatusSerializer 对齐）"""
    class Meta:
        model = DeviceStatusCollection
        fields = ['id', 'event_name', 'data', 'timestamp', 'received_at']
