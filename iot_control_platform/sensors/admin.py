from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.shortcuts import redirect
from django.contrib import messages
import json
from django.utils import timezone
from datetime import timedelta
from .models import SensorType, Sensor, SensorData, SensorStatusCollection
from resource_folders.models import ResourceFolder
from services.sensors_service.sensor_command_send_service import sensor_command_send_service


class SensorOnlineFilter(admin.SimpleListFilter):
    """基于 last_seen 实时判断在线状态，避免 is_online 字段未及时回写导致的过滤偏差"""
    title = '在线状态'
    parameter_name = 'online'

    def lookups(self, request, model_admin):
        return (('1', '在线'), ('0', '离线'))

    def queryset(self, request, queryset):
        # 与 Sensor.computed_is_online 阈值保持一致：3 分钟
        threshold = timezone.now() - timedelta(minutes=3)
        if self.value() == '1':
            return queryset.filter(last_seen__gte=threshold)
        if self.value() == '0':
            return queryset.filter(last_seen__lt=threshold) | queryset.filter(last_seen__isnull=True)
        return queryset


class SensorFolderFilter(admin.SimpleListFilter):
    """只展示传感器目录，并提供“未分类”入口。"""
    title = '文件夹'
    parameter_name = 'folder'

    def lookups(self, request, model_admin):
        folders = ResourceFolder.objects.filter(
            resource_type=ResourceFolder.SENSOR
        ).select_related('parent')
        return [('unfiled', '未分类')] + [(str(folder.pk), str(folder)) for folder in folders]

    def queryset(self, request, queryset):
        if self.value() == 'unfiled':
            return queryset.filter(folder__isnull=True)
        if self.value() and self.value().isdigit():
            return queryset.filter(folder_id=int(self.value()))
        return queryset


@admin.register(SensorType)
class SensorTypeAdmin(admin.ModelAdmin):
    """传感器类型管理"""
    
    list_display = ['SensorType_id', 'name', 'sensors_count', 'created_at']
    search_fields = ['SensorType_id', 'name', 'description']

    fieldsets = [
        ('基本信息', {
            'fields': ['SensorType_id', 'name', 'description']
        }),
        ('数据与配置', {
            'fields': ['data_fields', 'config_parameters', 'commands'],
            'description': '使用JSON列表格式，例如：["data"] 或 ["samplingInterval", "is_enabled"]'
        }),

    ]
    
    def sensors_count(self, obj):
        """传感器数量"""
        count = obj.sensors.count()
        return format_html('<b>{}</b>', count)
    sensors_count.short_description = '传感器数量'
    



@admin.register(Sensor)
class SensorAdmin(admin.ModelAdmin):
    """传感器管理"""
    
    list_display = ['sensor_id', 'name', 'sensor_type', 'folder_display', 'online_status', 'latest_data_time', 'created_at']
    list_filter = [SensorFolderFilter, 'sensor_type', SensorOnlineFilter, 'created_at']
    search_fields = ['sensor_id', 'name', 'description', 'folder__name']
    list_select_related = ['sensor_type', 'folder']
    readonly_fields = [
        'created_at', 'updated_at', 'last_seen', 'command_buttons_detail_display',
        'mqtt_topic_data', 'mqtt_topic_status', 'mqtt_topic_control',
    ]

    fieldsets = [
        ('基本信息', {
            'fields': ['sensor_id', 'name', 'sensor_type', 'description']
        }),
        ('目录管理', {
            'fields': ['folder', 'sort_order'],
            'description': '选择传感器管理页中的文件夹；留空表示“未分类”。'
        }),
        ('命令控制', {
            'fields': ['command_buttons_detail_display'],
            'description': '发送控制命令到该传感器（需先保存传感器；需在传感器类型中配置 commands）'
        }),
        ('MQTT配置', {
            'fields': ['mqtt_topic_data', 'mqtt_topic_status', 'mqtt_topic_control'],
            'description': '保存时会自动生成 data / status / control 三个主题'
        }),
        ('状态信息', {
            'fields': ['last_seen', 'location'],
            'description': 'last_seen 由系统自动维护，不可手动修改'
        }),
        ('时间戳', {
            'fields': ['created_at', 'updated_at'],
            'classes': ['collapse']
        }),
    ]
    def get_readonly_fields(self, request, obj=None):
        """确保命令控制区域在添加和编辑页都显示"""
        base = list(super().get_readonly_fields(request, obj))
        if 'command_buttons_detail_display' not in base:
            base.append('command_buttons_detail_display')
        return base

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Admin 中只允许选择传感器目录，防止跨资源类型归档。"""
        if db_field.name == 'folder':
            kwargs['queryset'] = ResourceFolder.objects.filter(
                resource_type=ResourceFolder.SENSOR
            ).select_related('parent')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description='文件夹', ordering='folder__name', empty_value='未分类')
    def folder_display(self, obj):
        return str(obj.folder) if obj.folder_id else None

    def online_status(self, obj):
        """基于 last_seen 动态计算在线状态"""
        is_online = obj.computed_is_online
        if is_online:
            return format_html(
                '<span style="color: green; font-weight: bold;">{}</span>',
                '● 在线'
            )
        elif obj.last_seen:
            time_diff = timezone.now() - obj.last_seen
            hours = int(time_diff.total_seconds() // 3600)
            if hours < 1:
                mins = int(time_diff.total_seconds() // 60)
                ago = f'{mins}分钟前'
            elif hours < 24:
                ago = f'{hours}小时前'
            else:
                ago = f'{hours // 24}天前'
            return format_html(
                '<span style="color: red;">● 离线 <small style="color: gray;">({} 上报)</small></span>',
                ago
            )
        else:
            return format_html('<span style="color: gray;">{}</span>', '● 从未上报')
    online_status.short_description = '在线状态'

    def latest_data_time(self, obj):
        """最新数据时间（基于 last_seen 或最新数据记录）"""
        ts = obj.last_seen
        if not ts:
            latest = obj.data_records.order_by('-received_at', '-pk').first()
            if latest:
                ts = latest.received_at
        if ts:
            time_diff = timezone.now() - ts
            if time_diff.total_seconds() < 300:
                color = 'green'
            elif time_diff.total_seconds() < 3600:
                color = 'orange'
            else:
                color = 'red'
            return format_html(
                '<span style="color: {};">{}</span>',
                color,
                timezone.localtime(ts).strftime('%Y-%m-%d %H:%M:%S')
            )
        return format_html('<span style="color: gray;">{}</span>', '无数据')
    latest_data_time.short_description = '最新数据时间'

    def change_view(self, request, object_id, form_url='', extra_context=None):
        """拦截 POST：当为发送命令时处理并重定向，否则走默认逻辑"""
        if request.method == 'POST' and request.POST.get('admin_send_command'):
            cmd_name = request.POST.get('admin_send_command')
            try:
                sensor = Sensor.objects.get(pk=object_id)
            except Sensor.DoesNotExist:
                messages.error(request, '传感器不存在')
                return redirect('admin:sensors_sensor_changelist')

            commands = sensor_command_send_service.show_sensor_control_commands(sensor)
            if cmd_name not in commands:
                messages.error(request, f'未定义的命令: {cmd_name}')
            else:
                command_info = commands[cmd_name]
                params = command_info.get('params') or []
                param_dict = {}
                for p in params:
                    val = request.POST.get(f'param_{p}')
                    if val is not None:
                        try:
                            param_dict[p] = int(val) if str(val).isdigit() else val
                        except (ValueError, TypeError):
                            param_dict[p] = val

                success = sensor_command_send_service.send_command(
                    sensor.sensor_id, cmd_name, param_dict if param_dict else None
                )
                desc = command_info.get('description', cmd_name)
                if success:
                    messages.success(request, f'命令「{desc}」已成功发送到 {sensor.sensor_id}')
                else:
                    messages.error(request, f'命令「{desc}」发送失败，请检查 MQTT 连接')

            return redirect('admin:sensors_sensor_change', object_id)

        return super().change_view(request, object_id, form_url, extra_context)

    def command_buttons_detail_display(self, obj):
        """在传感器详情页显示可用的命令按钮（通过主表单 POST，无额外 URL）"""
        if not obj or not obj.pk:
            return format_html('<p style="color: gray;">请先保存传感器以显示命令按钮</p>')

        commands = sensor_command_send_service.show_sensor_control_commands(obj)
        if not commands:
            return format_html(
                '<p style="color: gray;">该传感器类型「{}」未定义控制命令，可在传感器类型中配置 commands 字段</p>',
                obj.sensor_type.name if obj.sensor_type else '未知'
            )

        buttons = []
        for cmd_name, cmd_info in commands.items():
            desc = cmd_info.get('description', cmd_name)
            params = cmd_info.get('params') or []
            if params:
                inputs_html = mark_safe(''.join(
                    format_html('<label>{}: <input type="number" name="param_{}" value="120" style="width:80px"></label> ', p, p)
                    for p in params
                ))
                buttons.append(format_html(
                    '<span style="display:inline-flex;align-items:center;gap:4px;margin-right:12px">'
                    '{}<button type="submit" name="admin_send_command" value="{}" class="button">{}</button></span>',
                    inputs_html, cmd_name, desc
                ))
            else:
                buttons.append(format_html(
                    '<button type="submit" name="admin_send_command" value="{}" class="button" '
                    'onclick="return confirm(\'确定发送命令「{}」到传感器 {}？\');">{}</button>',
                    cmd_name, desc, obj.sensor_id, desc
                ))

        buttons_html = mark_safe(' '.join(str(b) for b in buttons))
        return format_html('<div style="display: flex; flex-wrap: wrap; gap: 8px;">{}</div>', buttons_html)
    command_buttons_detail_display.short_description = '控制命令'

@admin.register(SensorData)
class SensorDataAdmin(admin.ModelAdmin):
    """传感器数据管理"""
    
    list_display = ['sensor_id_display', 'data_preview', 'timestamp', 'received_at']
    list_filter = ['sensor__sensor_type', 'timestamp', 'received_at']
    search_fields = ['sensor__sensor_id', 'sensor__name']
    readonly_fields = ['sensor', 'data', 'timestamp', 'received_at']
    date_hierarchy = 'timestamp'
    
    fieldsets = [
        ('数据信息', {
            'fields': ['sensor', 'data', 'timestamp', 'received_at']
        }),
    ]
    
    def sensor_id_display(self, obj):
        """传感器ID"""
        return obj.sensor.sensor_id
    sensor_id_display.short_description = '传感器ID'
    sensor_id_display.admin_order_field = 'sensor__sensor_id'
    
    def data_preview(self, obj):
        """数据预览"""
        try:
            data_str = json.dumps(obj.data, ensure_ascii=False)
            if len(data_str) > 100:
                data_str = data_str[:100] + '...'
            return format_html('<code>{}</code>', data_str)
        except (TypeError, ValueError):
            return str(obj.data)
    data_preview.short_description = '数据内容'

    def has_add_permission(self, request):
        """禁止手动添加数据"""
        return False

    def has_change_permission(self, request, obj=None):
        """禁止修改数据"""
        return False


@admin.register(SensorStatusCollection)
class SensorStatusCollectionAdmin(admin.ModelAdmin):
    """传感器状态数据管理"""
    
    list_display = ['sensor_id_display', 'event_name', 'data_preview', 'timestamp', 'received_at']
    list_filter = ['sensor__sensor_type', 'event_name', 'timestamp', 'received_at']
    search_fields = ['sensor__sensor_id', 'sensor__name', 'event_name']
    readonly_fields = ['sensor', 'data', 'timestamp', 'event_name', 'received_at']
    date_hierarchy = 'timestamp'
    
    fieldsets = [
        ('状态信息', {
            'fields': ['sensor', 'event_name', 'data', 'timestamp', 'received_at']
        }),
    ]
    
    def sensor_id_display(self, obj):
        """传感器ID"""
        return obj.sensor.sensor_id
    sensor_id_display.short_description = '传感器ID'
    sensor_id_display.admin_order_field = 'sensor__sensor_id'
    
    def data_preview(self, obj):
        """数据预览"""
        try:
            data_str = json.dumps(obj.data, ensure_ascii=False)
            if len(data_str) > 100:
                data_str = data_str[:100] + '...'
            return format_html('<code>{}</code>', data_str)
        except (TypeError, ValueError):
            return str(obj.data)
    data_preview.short_description = '状态数据内容'
    
    def has_add_permission(self, request):
        """禁止手动添加数据"""
        return False
    
    def has_change_permission(self, request, obj=None):
        """禁止修改数据"""
        return False
