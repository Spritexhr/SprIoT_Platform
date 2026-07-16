from rest_framework import serializers

from .defaults import (
    DEFAULT_CONFIGS,
    ConfigValidationError,
    validate_config_value,
)
from .models import PlatformConfig, Plugin


SECRET_MASK = "********"
SECRET_CONFIG_KEYS = frozenset(
    item["key"] for item in DEFAULT_CONFIGS if item.get("secret", False)
)


def is_secret_config_key(key: str) -> bool:
    """判断配置键是否在预定义的敏感配置清单中。"""
    return key in SECRET_CONFIG_KEYS


class PlatformConfigSerializer(serializers.ModelSerializer):
    """平台配置序列化器。

    敏感值永不通过 API 回显。超级用户编辑表单把掩码原样提交时，保留数据库中的
    原值，避免通用表单在未修改密码时把占位符写进数据库。
    """

    secret = serializers.SerializerMethodField()

    class Meta:
        model = PlatformConfig
        fields = [
            "id", "key", "value", "category", "description", "secret",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "secret", "created_at", "updated_at"]

    def get_secret(self, obj):
        return is_secret_config_key(obj.key)

    def validate_key(self, value):
        if self.instance is not None and value != self.instance.key:
            raise serializers.ValidationError("配置键创建后不能修改")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        key = attrs.get("key", getattr(self.instance, "key", ""))
        if is_secret_config_key(key) and attrs.get("value") == SECRET_MASK:
            if self.instance is None:
                raise serializers.ValidationError({"value": "新建敏感配置时不能使用掩码作为值"})
            attrs.pop("value")
        if "value" in attrs:
            try:
                attrs["value"] = validate_config_value(key, attrs["value"])
            except ConfigValidationError as exc:
                raise serializers.ValidationError({"value": str(exc)}) from exc
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if is_secret_config_key(instance.key):
            data["value"] = SECRET_MASK
        return data


class PluginSerializer(serializers.ModelSerializer):
    """插件序列化器"""

    class Meta:
        model = Plugin
        fields = ["id", "name", "enabled", "version", "description", "installed_at", "updated_at"]
        read_only_fields = ["id", "name", "version", "description", "installed_at", "updated_at"]
