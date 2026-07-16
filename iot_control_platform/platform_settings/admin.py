"""平台配置 Admin。

PlatformConfig 可能包含 MQTT 密码。Admin 与 REST API 使用同一敏感字段规则：
只允许超级用户访问，列表和编辑表单都不回显已有密文。
"""
from django import forms
from django.contrib import admin

from .defaults import ConfigValidationError, validate_config_value
from .models import PlatformConfig, Plugin
from .serializers import SECRET_MASK, is_secret_config_key


class PlatformConfigAdminForm(forms.ModelForm):
    """敏感值用占位符显示；未修改占位符时保留数据库原值。"""

    class Meta:
        model = PlatformConfig
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and is_secret_config_key(self.instance.key):
            self.initial["value"] = SECRET_MASK

    def clean_key(self):
        key = self.cleaned_data["key"]
        if self.instance.pk and key != self.instance.key:
            raise forms.ValidationError("配置键创建后不能修改")
        return key

    def clean_value(self):
        value = self.cleaned_data.get("value")
        key = self.cleaned_data.get("key", self.instance.key)
        if (
            self.instance.pk
            and is_secret_config_key(key)
            and value == SECRET_MASK
        ):
            return type(self.instance).objects.only("value").get(
                pk=self.instance.pk
            ).value
        if not self.instance.pk and is_secret_config_key(key) and value == SECRET_MASK:
            raise forms.ValidationError("新建敏感配置时不能使用掩码作为值")
        try:
            return validate_config_value(key, value)
        except ConfigValidationError as exc:
            raise forms.ValidationError(str(exc)) from exc


@admin.register(PlatformConfig)
class PlatformConfigAdmin(admin.ModelAdmin):
    form = PlatformConfigAdminForm
    list_display = ["key", "value_short", "category", "description", "updated_at"]
    list_filter = ["category"]
    search_fields = ["key", "description"]
    ordering = ["category", "key"]

    def value_short(self, obj):
        if is_secret_config_key(obj.key):
            return SECRET_MASK
        val = obj.value
        if val is None:
            return "-"
        s = str(val)
        return s[:50] + "..." if len(s) > 50 else s

    value_short.short_description = "配置值"

    def has_module_permission(self, request):
        return bool(request.user and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def has_add_permission(self, request):
        return bool(request.user and request.user.is_superuser)

    def has_change_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)


@admin.register(Plugin)
class PluginAdmin(admin.ModelAdmin):
    list_display = ["name", "enabled", "version", "description", "updated_at"]
    list_filter = ["enabled"]
    search_fields = ["name", "description"]
    list_editable = ["enabled"]
    ordering = ["name"]
