"""
用户认证相关 API 视图
提供：注册、获取/更新用户信息、修改密码
JWT Token 的获取(登录)和刷新由 SimpleJWT 内置视图处理
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .auth_serializers import (
    ChangePasswordInputSerializer,
    RegisterInputSerializer,
    UserProfileInputSerializer,
)


User = get_user_model()


def _input_error_response(errors):
    """保持原认证 API 的 ``{"detail": "..."}`` 错误响应契约。"""

    def first_message(value):
        if isinstance(value, dict):
            for nested in value.values():
                message = first_message(nested)
                if message:
                    return message
        elif isinstance(value, (list, tuple)):
            for nested in value:
                message = first_message(nested)
                if message:
                    return message
        elif value:
            return str(value)
        return "请求参数无效"

    return Response(
        {"detail": first_message(errors)},
        status=status.HTTP_400_BAD_REQUEST,
    )


@api_view(['POST'])
@permission_classes([AllowAny])
def register(request):
    """
    用户注册
    请求体: { "username": "", "password": "", "password2": "", "email": "" }
    """
    serializer = RegisterInputSerializer(data=request.data)
    if not serializer.is_valid():
        return _input_error_response(serializer.errors)
    data = serializer.validated_data

    # exists() 只提供友好的提前提示；数据库唯一约束才是并发注册时的最终防线。
    # IntegrityError 必须在 atomic 块外捕获，否则当前事务会保持 broken 状态。
    try:
        with transaction.atomic():
            user = User.objects.create_user(
                username=data["username"],
                password=data["password"],
                email=data.get("email", ""),
                is_staff=False,
                is_superuser=False,
            )
    except IntegrityError:
        return Response(
            {"detail": "该用户名已被注册"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(
        {
            'detail': '注册成功',
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
            },
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def user_profile(request):
    """
    获取 / 更新当前用户信息
    GET  → 返回用户基本信息
    PUT  → 更新 email、first_name、last_name
    """
    user = request.user

    if request.method == 'GET':
        return Response({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'date_joined': user.date_joined,
            'is_staff': user.is_staff,
            'is_superuser': user.is_superuser,
        })

    # PUT 保持历史兼容：允许只提交需要修改的字段，未提交字段维持原值。
    serializer = UserProfileInputSerializer(
        user,
        data=request.data,
        partial=True,
        context={"user": user},
    )
    if not serializer.is_valid():
        return _input_error_response(serializer.errors)
    user = serializer.save()

    return Response({
        'detail': '更新成功',
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
        },
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def change_password(request):
    """
    修改密码
    请求体: { "old_password": "", "new_password": "", "new_password2": "" }
    """
    user = request.user
    serializer = ChangePasswordInputSerializer(data=request.data)
    if not serializer.is_valid():
        return _input_error_response(serializer.errors)
    data = serializer.validated_data
    old_password = data["old_password"]
    new_password = data["new_password"]
    new_password2 = data["new_password2"]

    if not user.check_password(old_password):
        return Response(
            {'detail': '原密码错误'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if new_password != new_password2:
        return Response(
            {'detail': '两次输入的新密码不一致'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        validate_password(new_password, user)
    except ValidationError as e:
        return Response(
            {'detail': e.messages[0]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user.set_password(new_password)
    user.save()

    return Response({'detail': '密码修改成功，请重新登录'})
