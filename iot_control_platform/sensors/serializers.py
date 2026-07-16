import json

from rest_framework import serializers
from .models import SensorType, Sensor, SensorData, SensorStatusCollection


def normalize_commands_payload(value):
    """规范化 SensorType.commands / DeviceType.commands 写入数据。
    要求顶层是 dict<command_name, info>，每条 info.mqtt_message 必须能解析成 dict。
    前端编辑器有时把 mqtt_message 整个序列化成 JSON 字符串保存，这里兜底解析回 dict；
    解析不动时报 400，避免脏数据进库导致命令调用时 .copy() 抛 AttributeError。
    """
    if value in (None, '', [], {}):
        return value
    if not isinstance(value, dict):
        raise serializers.ValidationError('commands 必须是 JSON 对象')
    cleaned = {}
    for cmd_name, info in value.items():
        if not isinstance(info, dict):
            raise serializers.ValidationError(f"命令 '{cmd_name}' 必须是 JSON 对象")
        info = dict(info)
        msg = info.get('mqtt_message')
        if isinstance(msg, str):
            try:
                msg = json.loads(msg)
            except (TypeError, ValueError):
                raise serializers.ValidationError(
                    f"命令 '{cmd_name}' 的 mqtt_message 不是合法 JSON")
        if msg is not None and not isinstance(msg, dict):
            raise serializers.ValidationError(
                f"命令 '{cmd_name}' 的 mqtt_message 必须是 JSON 对象，而不是 {type(msg).__name__}")
        if msg is not None:
            info['mqtt_message'] = msg
        cleaned[cmd_name] = info
    return cleaned


class SensorTypeSerializer(serializers.ModelSerializer):
    """传感器类型序列化器"""
    sensor_count = serializers.SerializerMethodField()

    class Meta:
        model = SensorType
        fields = [
            'id', 'SensorType_id', 'name', 'description',
            'data_fields', 'config_parameters', 'commands',
            'created_at', 'sensor_count',
        ]
        read_only_fields = ['id', 'created_at', 'sensor_count']

    def validate_commands(self, value):
        return normalize_commands_payload(value)

    def get_sensor_count(self, obj):
        # 优先使用 annotate 预计算的值，避免额外查询
        if hasattr(obj, '_sensor_count'):
            return obj._sensor_count
        return obj.sensors.count()


class SensorTypeBriefSerializer(serializers.ModelSerializer):
    """传感器类型简要序列化器（用于嵌套）"""
    class Meta:
        model = SensorType
        fields = ['id', 'SensorType_id', 'name', 'data_fields', 'config_parameters', 'commands']


class SensorListSerializer(serializers.ModelSerializer):
    """传感器列表序列化器（含类型信息和最新数据）"""
    sensor_type_info = SensorTypeBriefSerializer(source='sensor_type', read_only=True)
    latest_data = serializers.SerializerMethodField()
    is_online = serializers.SerializerMethodField()
    folder_info = serializers.SerializerMethodField()

    class Meta:
        model = Sensor
        fields = [
            'id', 'sensor_id', 'name', 'description', 'location',
            'mqtt_topic_data', 'mqtt_topic_status', 'mqtt_topic_control',
            'is_online', 'last_seen',
            'sort_order',
            'created_at', 'updated_at',
            'sensor_type', 'sensor_type_info', 'latest_data', 'folder', 'folder_info',
        ]

    def get_folder_info(self, obj):
        if not obj.folder_id:
            return None
        return {'id': obj.folder_id, 'name': obj.folder.name, 'parent': obj.folder.parent_id}

    def get_is_online(self, obj):
        """根据 last_seen 实时计算在线状态：3分钟内有数据视为在线"""
        return bool(obj.computed_is_online)

    def get_latest_data(self, obj):
        # API 列表/详情通过相关子查询注入，查询数不随资源数量增长。
        if hasattr(obj, '_latest_timestamp'):
            if obj._latest_timestamp is None:
                return None
            return {
                'data': obj._latest_data,
                'timestamp': obj._latest_timestamp,
            }
        # 兼容其它调用方显式提供的预取结果。
        if hasattr(obj, '_prefetched_objects_cache') and 'data_records' in obj._prefetched_objects_cache:
            records = obj._prefetched_objects_cache['data_records']
            if records:
                return {
                    'data': records[0].data,
                    'timestamp': records[0].timestamp,
                }
            return None
        # 回退到查询
        record = obj.data_records.order_by('-received_at', '-pk').first()
        if record:
            return {
                'data': record.data,
                'timestamp': record.timestamp,
            }
        return None


class SensorDetailSerializer(SensorListSerializer):
    """传感器详情序列化器"""
    data_count_24h = serializers.SerializerMethodField()

    class Meta(SensorListSerializer.Meta):
        fields = SensorListSerializer.Meta.fields + ['data_count_24h']

    def get_data_count_24h(self, obj):
        # 优先使用 annotate 预计算的值
        if hasattr(obj, '_data_count_24h'):
            return obj._data_count_24h
        from django.utils import timezone
        from datetime import timedelta
        start = timezone.now() - timedelta(hours=24)
        return obj.data_records.filter(received_at__gte=start).count()


class SensorCreateUpdateSerializer(serializers.ModelSerializer):
    """传感器创建/更新序列化器"""
    class Meta:
        model = Sensor
        fields = [
            'sensor_id', 'name', 'description', 'location', 'sensor_type', 'folder',
        ]

    def validate_folder(self, value):
        if value is not None and value.resource_type != 'sensor':
            raise serializers.ValidationError('请选择传感器文件夹')
        return value


class SensorDataSerializer(serializers.ModelSerializer):
    """传感器数据序列化器"""
    class Meta:
        model = SensorData
        fields = ['id', 'data', 'timestamp', 'received_at']


class SensorStatusSerializer(serializers.ModelSerializer):
    """传感器状态记录序列化器"""
    class Meta:
        model = SensorStatusCollection
        fields = ['id', 'data', 'timestamp', 'event_name', 'received_at']
