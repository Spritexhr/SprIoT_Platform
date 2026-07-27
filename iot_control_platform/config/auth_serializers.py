"""认证接口的输入序列化器。

认证视图不能直接对 ``request.data`` 中的任意 JSON 值调用字符串方法。这里使用
严格字符串字段，拒绝列表、对象、数字和 ``null``，同时把长度限制与 Django
用户模型保持一致。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers


User = get_user_model()
USERNAME_MAX_LENGTH = User._meta.get_field(User.USERNAME_FIELD).max_length
EMAIL_MAX_LENGTH = User._meta.get_field(User.EMAIL_FIELD).max_length
FIRST_NAME_MAX_LENGTH = User._meta.get_field("first_name").max_length
LAST_NAME_MAX_LENGTH = User._meta.get_field("last_name").max_length

# 防止异常大的请求值进入密码哈希器。该上限远高于正常密码长度，不会影响常规
# 使用；请求体本身仍由 Web/ASGI 层的总大小限制兜底。
PASSWORD_MAX_LENGTH = 1024


class StrictCharField(serializers.CharField):
    """只接受真正的 JSON 字符串，不隐式把数字转换成字符串。"""

    default_error_messages = {
        **serializers.CharField.default_error_messages,
        "invalid": "必须是字符串",
    }

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class RegisterInputSerializer(serializers.Serializer):
    username = StrictCharField(
        max_length=USERNAME_MAX_LENGTH,
        trim_whitespace=True,
        validators=[User.username_validator],
    )
    password = StrictCharField(
        max_length=PASSWORD_MAX_LENGTH,
        trim_whitespace=False,
        write_only=True,
    )
    password2 = StrictCharField(
        max_length=PASSWORD_MAX_LENGTH,
        trim_whitespace=False,
        write_only=True,
    )
    email = serializers.EmailField(
        required=False,
        allow_blank=True,
        max_length=EMAIL_MAX_LENGTH,
    )

    def validate(self, attrs):
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError(
                {"password2": "两次输入的密码不一致"}
            )

        username = attrs["username"]
        email = attrs.get("email", "")
        if User.objects.filter(username=username).exists():
            raise serializers.ValidationError(
                {"username": "该用户名已被注册"}
            )
        if email and User.objects.filter(email=email).exists():
            raise serializers.ValidationError(
                {"email": "该邮箱已被注册"}
            )

        # UserAttributeSimilarityValidator 只有收到候选用户时，才能比较用户名、
        # 邮箱等字段。候选对象不入库，仅用于完整执行 Django 密码策略。
        provisional_user = User(username=username, email=email)
        try:
            validate_password(attrs["password"], provisional_user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"password": exc.messages}
            ) from exc
        return attrs


class UserProfileInputSerializer(serializers.Serializer):
    email = serializers.EmailField(
        required=False,
        allow_blank=True,
        max_length=EMAIL_MAX_LENGTH,
    )
    first_name = StrictCharField(
        required=False,
        allow_blank=True,
        max_length=FIRST_NAME_MAX_LENGTH,
    )
    last_name = StrictCharField(
        required=False,
        allow_blank=True,
        max_length=LAST_NAME_MAX_LENGTH,
    )

    def validate_email(self, value):
        user = self.context["user"]
        if (
            value
            and User.objects.filter(email=value).exclude(pk=user.pk).exists()
        ):
            raise serializers.ValidationError("该邮箱已被其他用户使用")
        return value

    def update(self, instance, validated_data):
        changed_fields = []
        for field, value in validated_data.items():
            setattr(instance, field, value)
            changed_fields.append(field)
        if changed_fields:
            instance.save(update_fields=changed_fields)
        return instance

    def create(self, validated_data):  # pragma: no cover - 仅用于明确接口用途
        raise NotImplementedError("用户资料序列化器只支持更新")


class ChangePasswordInputSerializer(serializers.Serializer):
    old_password = StrictCharField(
        max_length=PASSWORD_MAX_LENGTH,
        trim_whitespace=False,
        write_only=True,
    )
    new_password = StrictCharField(
        max_length=PASSWORD_MAX_LENGTH,
        trim_whitespace=False,
        write_only=True,
    )
    new_password2 = StrictCharField(
        max_length=PASSWORD_MAX_LENGTH,
        trim_whitespace=False,
        write_only=True,
    )
