"""认证接口输入边界与并发回归测试。"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from config.auth_serializers import PASSWORD_MAX_LENGTH


User = get_user_model()


class RegisterInputValidationTests(APITestCase):
    url = reverse("register")
    valid_password = "Granite-Comet_927!Pass"

    def _payload(self, **overrides):
        payload = {
            "username": "new-user",
            "password": self.valid_password,
            "password2": self.valid_password,
            "email": "new-user@example.com",
        }
        payload.update(overrides)
        return payload

    def test_null_and_oversized_fields_return_400_without_creating_user(self):
        payloads = (
            self._payload(email=None),
            self._payload(username="u" * 151),
            self._payload(password="p" * (PASSWORD_MAX_LENGTH + 1)),
        )

        for payload in payloads:
            with self.subTest(payload=list(payload)):
                response = self.client.post(self.url, payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("detail", response.data)

        self.assertFalse(User.objects.exists())

    def test_password_similarity_validation_uses_provisional_user(self):
        password = "similar-user-2026!"
        response = self.client.post(
            self.url,
            self._payload(
                username="similar-user-2026",
                password=password,
                password2=password,
                email="different@example.com",
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)
        self.assertFalse(User.objects.exists())

    def test_unique_constraint_race_returns_400(self):
        with patch(
            "config.auth_views.User.objects.create_user",
            side_effect=IntegrityError("duplicate username"),
        ):
            response = self.client.post(
                self.url,
                self._payload(),
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], "该用户名已被注册")


class ProfileInputValidationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="profile-owner",
            password="Slate-Otter_472!Pass",
            email="before@example.com",
            first_name="Before",
            last_name="Owner",
        )
        self.client.force_authenticate(self.user)
        self.url = reverse("user-profile")

    def test_non_string_null_and_oversized_fields_return_400(self):
        payloads = (
            {"email": ["not", "a", "string"]},
            {"first_name": None},
            {"last_name": "n" * 151},
        )

        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.client.put(self.url, payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("detail", response.data)

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "before@example.com")
        self.assertEqual(self.user.first_name, "Before")
        self.assertEqual(self.user.last_name, "Owner")

    def test_partial_put_preserves_omitted_fields(self):
        response = self.client.put(
            self.url,
            {"first_name": "After"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "After")
        self.assertEqual(self.user.email, "before@example.com")
        self.assertEqual(self.user.last_name, "Owner")


class ChangePasswordInputValidationTests(APITestCase):
    def setUp(self):
        self.old_password = "Slate-Otter_472!Pass"
        self.user = User.objects.create_user(
            username="password-owner",
            password=self.old_password,
        )
        self.client.force_authenticate(self.user)
        self.url = reverse("change-password")

    def _payload(self, **overrides):
        payload = {
            "old_password": self.old_password,
            "new_password": "Copper-Lantern_835!New",
            "new_password2": "Copper-Lantern_835!New",
        }
        payload.update(overrides)
        return payload

    def test_non_string_null_and_oversized_passwords_return_400(self):
        payloads = (
            self._payload(old_password=["wrong-type"]),
            self._payload(new_password=None),
            self._payload(
                new_password="n" * (PASSWORD_MAX_LENGTH + 1),
                new_password2="n" * (PASSWORD_MAX_LENGTH + 1),
            ),
        )

        for payload in payloads:
            with self.subTest(keys=list(payload)):
                response = self.client.post(self.url, payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("detail", response.data)

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.old_password))
