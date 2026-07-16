"""
自动化规则管理后台
基于 automation.models.AutomationRule，与 devices.Device、sensors.Sensor 对接
"""
import logging

from django.contrib import admin, messages
from django.utils.html import format_html, mark_safe
from .models import AutomationRule
from .execution_policy import (
    AutomationScriptExecutionDisabled,
    SCRIPT_EXECUTION_DISABLED_MESSAGE,
    ensure_script_execution_enabled,
)

logger = logging.getLogger(__name__)


@admin.register(AutomationRule)
class AutomationRuleAdmin(admin.ModelAdmin):
    """自动化规则管理"""

    list_display = [
        'name',
        'script_id',
        'process_status_display',
        'device_count_display',
        'created_at',
        'updated_at',
    ]
    list_display_links = ['name']
    list_filter = ['is_launched', 'process_status', 'created_at']
    search_fields = ['name', 'script_id', 'description', 'script']
    readonly_fields = ['created_at', 'updated_at', 'device_list_preview',
                       'is_launched', 'process_status', 'error_message']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('基本信息', {
            'fields': ('name', 'script_id', 'description'),
        }),
        ('轮询状态', {
            'fields': ('is_launched', 'poll_interval', 'process_status', 'error_message'),
            'description': '由前端轮询控制自动维护。poll_interval 可手动调整。',
        }),
        ('设备与传感器列表', {
            'fields': ('device_list', 'device_list_preview'),
            'description': 'device_id 需与 devices.Device 或 sensors.Sensor 对应。',
        }),
        ('脚本', {
            'fields': ('script',),
            'description': (
                '脚本与 Django 进程同权限运行，不是安全沙箱；仅可保存并执行受信任代码。'
                'devices 字典提供 current_state、Device 提供 send_command()。'
            ),
        }),
        ('时间信息', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    actions = ['test_execute']

    def has_add_permission(self, request):
        return bool(request.user and request.user.is_superuser)

    def has_change_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def process_status_display(self, obj):
        """轮询状态彩色指示器"""
        status_map = {
            'idle': ('gray', '未启动'),
            'running': ('green', '正在运行'),
            'stopped_by_user': ('orange', '由用户停止'),
            'error_stopped': ('red', '由错误停止'),
        }
        color, label = status_map.get(obj.process_status, ('gray', obj.process_status))
        if obj.process_status == 'running' and obj.is_launched:
            return format_html('<span style="color: {}; font-weight: bold;">{}</span>', color, label)
        return format_html('<span style="color: {};">{}</span>', color, label)
    process_status_display.short_description = '轮询状态'
    process_status_display.admin_order_field = 'process_status'

    def device_count_display(self, obj):
        """关联设备数量与摘要"""
        count = obj.get_device_count()
        summary = obj.get_device_summary()
        if count == 0:
            return format_html('<span style="color: red;">{}</span>', '⚠ 无设备')
        return format_html(
            '<span style="color: green; font-weight: bold;">{}</span><br><span style="color: gray; font-size: 0.9em;">{}</span>',
            f'{count} 个',
            summary,
        )
    device_count_display.short_description = '关联设备'

    def device_list_preview(self, obj):
        """设备列表预览，校验 device_id 是否存在于 Sensor/Device 表"""
        from sensors.models import Sensor
        from devices.models import Device

        if obj is None:
            return format_html('<span style="color: gray;">{}</span>', '保存后预览')
        if not obj.device_list or not isinstance(obj.device_list, list):
            return format_html('<span style="color: gray;">{}</span>', '暂无设备')

        html_parts = ['<div style="background: #f8f9fa; padding: 10px; border-radius: 4px;">']
        html_parts.append('<table style="width: 100%; border-collapse: collapse;">')
        html_parts.append('<tr style="background: #dee2e6; font-weight: bold;">')
        html_parts.append('<th style="padding: 5px; text-align: left;">设备ID</th>')
        html_parts.append('<th style="padding: 5px; text-align: left;">类型</th>')
        html_parts.append('<th style="padding: 5px; text-align: left;">名称</th>')
        html_parts.append('<th style="padding: 5px; text-align: left;">验证</th>')
        html_parts.append('</tr>')

        for item in obj.device_list:
            if not isinstance(item, dict):
                continue
            device_id = item.get('device_id', '-')
            device_type = item.get('device_type', '-')
            name = item.get('name', '-')

            type_color = '#4dabf7' if device_type == 'Sensor' else '#51cf66'
            exists = False
            try:
                if (device_type or '').strip().lower() == 'sensor':
                    Sensor.objects.get(device_id=device_id)
                else:
                    Device.objects.get(device_id=device_id)
                exists = True
            except (Sensor.DoesNotExist, Device.DoesNotExist, Exception):
                pass

            valid_text = '✓ 已存在' if exists else '✗ 未找到'
            valid_color = 'green' if exists else 'red'

            row = format_html(
                '<tr style="border-bottom: 1px solid #dee2e6;">'
                '<td style="padding: 5px; font-family: monospace;">{}</td>'
                '<td style="padding: 5px;"><span style="color: {}; font-weight: bold;">{}</span></td>'
                '<td style="padding: 5px;">{}</td>'
                '<td style="padding: 5px; color: {};">{}</td></tr>',
                device_id, type_color, device_type, name, valid_color, valid_text,
            )
            html_parts.append(str(row))

        html_parts.append('</table>')
        html_parts.append('</div>')
        return mark_safe(''.join(html_parts))
    device_list_preview.short_description = '设备列表预览'

    def test_execute(self, request, queryset):
        """测试执行规则"""
        if not request.user.is_superuser:
            self.message_user(request, '仅超级用户可执行自动化脚本。', level=messages.ERROR)
            return
        try:
            ensure_script_execution_enabled()
        except AutomationScriptExecutionDisabled:
            self.message_user(request, SCRIPT_EXECUTION_DISABLED_MESSAGE, level=messages.ERROR)
            return

        success_count = 0
        fail_count = 0
        for rule in queryset:
            try:
                if rule.execute():
                    success_count += 1
                else:
                    fail_count += 1
            except Exception:
                logger.exception("后台测试执行自动化规则失败 [%s]", rule.name)
                fail_count += 1
        self.message_user(
            request,
            f'测试执行完成：成功 {success_count} 条，失败 {fail_count} 条',
        )
    test_execute.short_description = '测试执行规则'
