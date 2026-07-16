"""
设备管理后台配置
参考 sensors/admin 结构实现
"""
from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.shortcuts import redirect
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta
from .models import DeviceType, Device, DeviceStatusCollection
from .online_status import get_device_offline_timeout
from resource_folders.models import ResourceFolder
from services.devices_service.device_command_send_service import device_command_send_service


class DeviceOnlineFilter(admin.SimpleListFilter):
    """基于 last_seen 实时判断在线状态，避免 is_online 字段未及时回写导致的过滤偏差"""
    title = '在线状态'
    parameter_name = 'online'

    def lookups(self, request, model_admin):
        return (('1', '在线'), ('0', '离线'))

    def queryset(self, request, queryset):
        threshold = timezone.now() - timedelta(
            seconds=get_device_offline_timeout()
        )
        if self.value() == '1':
            return queryset.filter(last_seen__gte=threshold)
        if self.value() == '0':
            return queryset.filter(last_seen__lt=threshold) | queryset.filter(last_seen__isnull=True)
        return queryset


class DeviceFolderFilter(admin.SimpleListFilter):
    """只展示设备目录，并提供“未分类”入口。"""
    title = '文件夹'
    parameter_name = 'folder'

    def lookups(self, request, model_admin):
        folders = ResourceFolder.objects.filter(
            resource_type=ResourceFolder.DEVICE
        ).select_related('parent')
        return [('unfiled', '未分类')] + [(str(folder.pk), str(folder)) for folder in folders]

    def queryset(self, request, queryset):
        if self.value() == 'unfiled':
            return queryset.filter(folder__isnull=True)
        if self.value() and self.value().isdigit():
            return queryset.filter(folder_id=int(self.value()))
        return queryset


@admin.register(DeviceType)
class DeviceTypeAdmin(admin.ModelAdmin):
    """设备类型管理"""

    list_display = ['DeviceType_id', 'name', 'devices_count', 'created_at']
    search_fields = ['DeviceType_id', 'name', 'description']

    fieldsets = [
        ('基本信息', {
            'fields': ['DeviceType_id', 'name', 'description']
        }),
        ('状态与配置', {
            'fields': ['config_parameters', 'commands'],
            'description': '使用JSON格式。config_parameters 是该类型设备所有可读字段名（状态值与配置项合并）。commands 格式参见 SensorType。'
        }),
    ]

    def devices_count(self, obj):
        """设备数量"""
        count = obj.devices.count()
        return format_html('<b>{}</b>', count)
    devices_count.short_description = '设备数量'


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    """设备管理"""

    list_display = ['device_id', 'name', 'device_type', 'folder_display', 'latest_data_time', 'online_indicator', 'created_at']
    list_filter = [DeviceFolderFilter, 'device_type', DeviceOnlineFilter, 'created_at']
    search_fields = ['device_id', 'name', 'description', 'folder__name']
    list_select_related = ['device_type', 'folder']
    readonly_fields = [
        'created_at', 'updated_at',
        'mqtt_topic_data', 'mqtt_topic_control',
        'last_seen',
        'command_buttons_detail_display',
    ]

    fieldsets = [
        ('基本信息', {
            'fields': ['device_id', 'name', 'device_type', 'description', 'location']
        }),
        ('目录管理', {
            'fields': ['folder', 'sort_order'],
            'description': '选择设备管理页中的文件夹；留空表示“未分类”。'
        }),
        ('命令控制', {
            'fields': ['command_buttons_detail_display'],
            'description': '发送控制命令到该设备（需先保存设备；需在设备类型中配置 commands）'
        }),
        ('MQTT配置', {
            'fields': ['mqtt_topic_data', 'mqtt_topic_control'],
            'description': '保存时自动生成，格式：iot/devices/{device_id}/xxx'
        }),
        ('状态信息', {
            'fields': ['last_seen'],
            'description': 'last_seen 由系统自动维护，不可手动修改；实时在线状态见列表页'
        }),
        ('时间戳', {
            'fields': ['created_at', 'updated_at'],
            'classes': ['collapse']
        }),
    ]

    actions = ['check_online_status']

    def get_readonly_fields(self, request, obj=None):
        """确保命令控制区域在添加和编辑页都显示"""
        base = list(super().get_readonly_fields(request, obj))
        if 'command_buttons_detail_display' not in base:
            base.append('command_buttons_detail_display')
        return base

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Admin 中只允许选择设备目录，防止跨资源类型归档。"""
        if db_field.name == 'folder':
            kwargs['queryset'] = ResourceFolder.objects.filter(
                resource_type=ResourceFolder.DEVICE
            ).select_related('parent')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description='文件夹', ordering='folder__name', empty_value='未分类')
    def folder_display(self, obj):
        return str(obj.folder) if obj.folder_id else None

    def latest_data_time(self, obj):
        """最新数据时间（优先用 last_seen，避免每行触发 N+1 查询）"""
        ts = obj.last_seen
        if not ts:
            latest = obj.status_records.order_by('-received_at', '-pk').first()
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

    def online_indicator(self, obj):
        """基于 last_seen 实时计算在线状态，与 sensors.online_status 行为对齐"""
        is_online = obj.computed_is_online
        if is_online:
            return format_html(
                '<span style="color: green; font-weight: bold;">{}</span>',
                '● 在线'
            )
        if obj.last_seen:
            time_diff = timezone.now() - obj.last_seen
            total_seconds = time_diff.total_seconds()
            if total_seconds < 3600:
                ago = f'{int(total_seconds // 60)}分钟前'
            elif total_seconds < 86400:
                ago = f'{int(total_seconds // 3600)}小时前'
            else:
                ago = f'{int(total_seconds // 86400)}天前'
            return format_html(
                '<span style="color: red;">● 离线 <small style="color: gray;">({} 上报)</small></span>',
                ago
            )
        return format_html('<span style="color: gray;">{}</span>', '● 从未上报')
    online_indicator.short_description = '在线状态'

    def check_online_status(self, request, queryset):
        """检查在线状态"""
        for device in queryset:
            device.check_online_status()
        self.message_user(request, f'已检查 {queryset.count()} 个设备的在线状态')
    check_online_status.short_description = '检查在线状态'

    def change_view(self, request, object_id, form_url='', extra_context=None):
        """拦截 POST：当为发送命令时处理并重定向，否则走默认逻辑"""
        if request.method == 'POST' and request.POST.get('admin_send_command'):
            cmd_name = request.POST.get('admin_send_command')
            try:
                device = Device.objects.get(pk=object_id)
            except Device.DoesNotExist:
                messages.error(request, '设备不存在')
                return redirect('admin:devices_device_changelist')

            commands = device_command_send_service.show_device_control_commands(device)
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

                success = device_command_send_service.send_command(
                    device.device_id, cmd_name, param_dict if param_dict else None
                )
                desc = command_info.get('description', cmd_name)
                if success:
                    messages.success(request, f'命令「{desc}」已成功发送到 {device.device_id}')
                else:
                    messages.error(request, f'命令「{desc}」发送失败，请检查 MQTT 连接')

            return redirect('admin:devices_device_change', object_id)

        return super().change_view(request, object_id, form_url, extra_context)

    def command_buttons_detail_display(self, obj):
        """在设备详情页显示可用的命令按钮（通过主表单 POST，无额外 URL）"""
        if not obj or not obj.pk:
            return format_html('<p style="color: gray;">{}</p>', '请先保存设备以显示命令按钮')

        commands = device_command_send_service.show_device_control_commands(obj)
        if not commands:
            return format_html(
                '<p style="color: gray;">该设备类型「{}」未定义控制命令，可在设备类型中配置 commands 字段</p>',
                obj.device_type.name if obj.device_type else '未知'
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
                    'onclick="return confirm(\'确定发送命令「{}」到设备 {}？\');">{}</button>',
                    cmd_name, desc, obj.device_id, desc
                ))

        buttons_html = mark_safe(' '.join(str(b) for b in buttons))
        return format_html('<div style="display: flex; flex-wrap: wrap; gap: 8px;">{}</div>', buttons_html)
    command_buttons_detail_display.short_description = '控制命令'


@admin.register(DeviceStatusCollection)
class DeviceStatusCollectionAdmin(admin.ModelAdmin):
    """设备状态记录管理"""

    list_display = ['device_device_id', 'event_name', 'data_preview', 'timestamp', 'received_at']
    list_filter = ['device__device_type', 'event_name', 'timestamp', 'received_at']
    search_fields = ['device__device_id', 'device__name', 'event_name']
    readonly_fields = ['device', 'event_name', 'data', 'timestamp', 'received_at']
    date_hierarchy = 'timestamp'

    fieldsets = [
        ('状态信息', {
            'fields': ['device', 'event_name', 'data', 'timestamp', 'received_at']
        }),
    ]

    def device_device_id(self, obj):
        """设备ID"""
        return obj.device.device_id
    device_device_id.short_description = '设备ID'
    device_device_id.admin_order_field = 'device__device_id'

    def data_preview(self, obj):
        """状态内容预览"""
        import json
        try:
            data_str = json.dumps(obj.data, ensure_ascii=False)
            if len(data_str) > 100:
                data_str = data_str[:100] + '...'
            return format_html('<code>{}</code>', data_str)
        except Exception:
            return str(obj.data)
    data_preview.short_description = '状态内容'

    def has_add_permission(self, request):
        """禁止手动添加状态记录"""
        return False

    def has_change_permission(self, request, obj=None):
        """禁止修改状态记录"""
        return False
