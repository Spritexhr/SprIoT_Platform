"""认证与生产配置的安全回归测试。

已确认但尚未修复的漏洞使用 ``expectedFailure`` 挂账。修复落地后，这些用例会
变成 unexpected success 并使测试失败，提醒维护者移除标记、把它们转为普通回归
测试，而不会把当前不安全行为固化成正确结果。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import expectedFailure

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, TransactionTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from services.realtime.middleware import _authenticate


class ProductionSettingsSecurityTests(SimpleTestCase):
    """生产环境必须在导入 settings 时 fail closed。"""

    def test_production_startup_rejects_missing_secret_key(self):
        project_dir = Path(__file__).resolve().parent.parent
        environment = os.environ.copy()
        environment.pop("SECRET_KEY", None)
        environment["DEBUG"] = "False"
        environment["PYTHONPATH"] = str(project_dir)

        result = subprocess.run(
            [sys.executable, "-c", "import config.settings"],
            cwd=project_dir,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("生产环境必须设置 SECRET_KEY", result.stderr)


class RegistrationSecurityTests(APITestCase):
    """匿名注册不得成为提权入口，并应稳健处理恶意 JSON 类型。"""

    def test_registration_ignores_privilege_escalation_fields(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "ordinary-register-user",
                "password": "Strong-Registration-Password-2026!",
                "password2": "Strong-Registration-Password-2026!",
                "email": "ordinary@example.com",
                "is_staff": True,
                "is_superuser": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = get_user_model().objects.get(username="ordinary-register-user")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_registration_rejects_non_string_username_with_400(self):
        self.client.raise_request_exception = False
        response = self.client.post(
            reverse("register"),
            {
                "username": ["not", "a", "string"],
                "password": "Strong-Registration-Password-2026!",
                "password2": "Strong-Registration-Password-2026!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class JwtRevocationSecurityTests(APITestCase):
    @expectedFailure
    def test_password_change_revokes_preexisting_refresh_token(self):
        # 当前未启用 SimpleJWT blacklist / password-revocation claim，旧 refresh
        # token 在密码修改后仍可换取新 access token。
        user = get_user_model().objects.create_user(
            username="refresh-token-owner",
            password="Quasar-Eel_927!Old",
        )
        refresh = RefreshToken.for_user(user)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}"
        )

        changed = self.client.post(
            reverse("change-password"),
            {
                "old_password": "Quasar-Eel_927!Old",
                "new_password": "Copper-Night_481?New",
                "new_password2": "Copper-Night_481?New",
            },
            format="json",
        )
        self.assertEqual(changed.status_code, status.HTTP_200_OK)

        self.client.credentials()
        refreshed = self.client.post(
            reverse("token-refresh"),
            {"refresh": str(refresh)},
            format="json",
        )
        self.assertEqual(refreshed.status_code, status.HTTP_401_UNAUTHORIZED)


class WebSocketJwtSecurityTests(TransactionTestCase):
    """鉴权查询会进入 database_sync_to_async，测试数据必须真实提交。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="websocket-auth-user",
            password="unused-test-password",
        )

    def test_active_user_access_token_is_accepted(self):
        token = AccessToken.for_user(self.user)

        authenticated = async_to_sync(_authenticate)(str(token))

        self.assertEqual(authenticated.pk, self.user.pk)
        self.assertTrue(authenticated.is_active)

    def test_refresh_token_is_rejected_during_websocket_handshake(self):
        token = RefreshToken.for_user(self.user)

        authenticated = async_to_sync(_authenticate)(str(token))

        self.assertIsInstance(authenticated, AnonymousUser)

    def test_inactive_user_access_token_is_rejected(self):
        # REST / WebSocket 必须复用同一套 active-user 校验。
        token = AccessToken.for_user(self.user)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        authenticated = async_to_sync(_authenticate)(str(token))

        self.assertIsInstance(authenticated, AnonymousUser)
