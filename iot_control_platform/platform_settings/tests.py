from io import StringIO

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .admin import PlatformConfigAdmin, PlatformConfigAdminForm
from .defaults import get_meta
from .models import PlatformConfig
from .serializers import SECRET_MASK


class PlatformConfigSecretApiTests(APITestCase):
    """敏感配置不能通过列表、详情或通用编辑表单泄露。"""

    def setUp(self):
        self.normal_user = get_user_model().objects.create_user(
            username="config-viewer",
            password="test-password-123",
        )
        self.superuser = get_user_model().objects.create_superuser(
            username="config-root",
            password="test-password-123",
            email="root@example.com",
        )
        self.public_config = PlatformConfig.objects.create(
            key="site_name",
            value="SprIoT",
            category="general",
            description="站点名称",
        )
        self.secret_config = PlatformConfig.objects.create(
            key="mqtt_password",
            value="mqtt-real-password",
            category="mqtt",
            description="MQTT 密码",
        )

    @staticmethod
    def _rows(response):
        return response.data.get("results", response.data)

    def test_anonymous_user_cannot_read_configs(self):
        response = self.client.get(reverse("platform-config-list"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_registered_user_only_reads_non_secret_configs(self):
        self.client.force_authenticate(self.normal_user)

        list_response = self.client.get(reverse("platform-config-list"))
        public_response = self.client.get(
            reverse("platform-config-detail", args=[self.public_config.key])
        )
        secret_response = self.client.get(
            reverse("platform-config-detail", args=[self.secret_config.key])
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        rows = self._rows(list_response)
        self.assertEqual([row["key"] for row in rows], [self.public_config.key])
        self.assertEqual(rows[0]["value"], "SprIoT")
        self.assertFalse(rows[0]["secret"])
        self.assertEqual(public_response.status_code, status.HTTP_200_OK)
        self.assertEqual(secret_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_registered_user_cannot_write_configs(self):
        self.client.force_authenticate(self.normal_user)

        create_response = self.client.post(
            reverse("platform-config-list"),
            {"key": "new_config", "value": "x", "category": "general"},
            format="json",
        )
        update_response = self.client.patch(
            reverse("platform-config-detail", args=[self.public_config.key]),
            {"value": "changed"},
            format="json",
        )
        delete_response = self.client.delete(
            reverse("platform-config-detail", args=[self.public_config.key])
        )

        self.assertEqual(create_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(update_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(delete_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_superuser_reads_secret_as_mask_only(self):
        self.client.force_authenticate(self.superuser)

        list_response = self.client.get(reverse("platform-config-list"))
        detail_response = self.client.get(
            reverse("platform-config-detail", args=[self.secret_config.key])
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        secret_row = next(
            row for row in self._rows(list_response) if row["key"] == self.secret_config.key
        )
        self.assertEqual(secret_row["value"], SECRET_MASK)
        self.assertTrue(secret_row["secret"])
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data["value"], SECRET_MASK)
        self.assertNotContains(detail_response, "mqtt-real-password")

    def test_superuser_submitting_mask_preserves_secret(self):
        self.client.force_authenticate(self.superuser)

        response = self.client.patch(
            reverse("platform-config-detail", args=[self.secret_config.key]),
            {"value": SECRET_MASK},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["value"], SECRET_MASK)
        self.secret_config.refresh_from_db()
        self.assertEqual(self.secret_config.value, "mqtt-real-password")

    def test_superuser_can_replace_secret_without_echoing_it(self):
        self.client.force_authenticate(self.superuser)

        response = self.client.patch(
            reverse("platform-config-detail", args=[self.secret_config.key]),
            {"value": "mqtt-new-password"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["value"], SECRET_MASK)
        self.assertNotContains(response, "mqtt-new-password")
        self.secret_config.refresh_from_db()
        self.assertEqual(self.secret_config.value, "mqtt-new-password")

    def test_schema_never_exposes_secret_default(self):
        self.client.force_authenticate(self.normal_user)

        response = self.client.get(reverse("platform-config-schema"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mqtt_password = next(
            item for item in response.data["items"] if item["key"] == "mqtt_password"
        )
        self.assertTrue(mqtt_password["secret"])
        self.assertEqual(mqtt_password["default"], SECRET_MASK)

    def test_admin_masks_secret_and_is_superuser_only(self):
        model_admin = PlatformConfigAdmin(PlatformConfig, AdminSite())
        staff = get_user_model().objects.create_user(
            username="config-staff",
            password="test-password-123",
            is_staff=True,
        )
        request_factory = RequestFactory()
        staff_request = request_factory.get("/admin/platform-settings/")
        staff_request.user = staff
        root_request = request_factory.get("/admin/platform-settings/")
        root_request.user = self.superuser

        self.assertFalse(model_admin.has_module_permission(staff_request))
        self.assertFalse(model_admin.has_view_permission(staff_request))
        self.assertFalse(model_admin.has_change_permission(staff_request))
        self.assertTrue(model_admin.has_module_permission(root_request))
        self.assertTrue(model_admin.has_view_permission(root_request))
        self.assertEqual(model_admin.value_short(self.secret_config), SECRET_MASK)
        self.assertNotIn("mqtt-real-password", model_admin.value_short(self.secret_config))

        form = PlatformConfigAdminForm(instance=self.secret_config)
        self.assertEqual(form.initial["value"], SECRET_MASK)
        self.assertNotIn("mqtt-real-password", form.as_p())

    def test_admin_mask_submission_preserves_secret(self):
        form = PlatformConfigAdminForm(
            data={
                "key": self.secret_config.key,
                "value": f'"{SECRET_MASK}"',
                "category": self.secret_config.category,
                "description": self.secret_config.description,
            },
            instance=self.secret_config,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.secret_config.refresh_from_db()
        self.assertEqual(self.secret_config.value, "mqtt-real-password")


class PlatformConfigValidationApiTests(APITestCase):
    """已知 key 的 API 类型/范围与 CLI 使用相同规则。"""

    initial_values = {
        "mqtt_broker": "broker.internal",
        "mqtt_port": 1883,
        "mqtt_keepalive": 60,
        "mqtt_username": "runner",
        "mqtt_password": "secret",
        "device_offline_timeout": 300,
        "device_reconnect_attempts": 3,
        "device_reconnect_interval": 10,
        "sensor_data_retention_days": 30,
        "device_data_retention_days": 30,
    }

    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser(
            username="validation-root",
            password="test-password-123",
            email="validation@example.com",
        )
        self.client.force_authenticate(self.superuser)
        for key, value in self.initial_values.items():
            meta = get_meta(key)
            PlatformConfig.objects.create(
                key=key,
                value=value,
                category=meta["category"],
                description=meta["description"],
            )

    def test_invalid_known_values_return_400_and_do_not_change_database(self):
        cases = (
            ("mqtt_broker", ""),
            ("mqtt_broker", "   "),
            ("mqtt_broker", 123),
            ("mqtt_port", True),       # bool 是 int 子类，但不能冒充端口
            ("mqtt_port", "1883"),
            ("mqtt_port", 0),
            ("mqtt_port", 65536),
            ("mqtt_keepalive", 0),
            ("mqtt_keepalive", 65536),
            ("mqtt_username", 123),
            ("mqtt_password", False),
            ("device_offline_timeout", 0),
            ("device_offline_timeout", 86401),
            ("device_reconnect_attempts", 0),
            ("device_reconnect_attempts", 101),
            ("device_reconnect_interval", 0),
            ("device_reconnect_interval", 3601),
            ("sensor_data_retention_days", 0),
            ("sensor_data_retention_days", 3651),
            ("device_data_retention_days", 0),
            ("device_data_retention_days", 3651),
        )

        for key, invalid_value in cases:
            with self.subTest(key=key, value=invalid_value):
                response = self.client.patch(
                    reverse("platform-config-detail", args=[key]),
                    {"value": invalid_value},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("value", response.data)
                self.assertEqual(
                    PlatformConfig.objects.get(key=key).value,
                    self.initial_values[key],
                )

    def test_valid_boundaries_and_broker_normalization_are_persisted(self):
        broker_response = self.client.patch(
            reverse("platform-config-detail", args=["mqtt_broker"]),
            {"value": "  mqtt.example.internal  "},
            format="json",
        )
        port_response = self.client.patch(
            reverse("platform-config-detail", args=["mqtt_port"]),
            {"value": 65535},
            format="json",
        )
        retention_response = self.client.patch(
            reverse("platform-config-detail", args=["sensor_data_retention_days"]),
            {"value": 1},
            format="json",
        )

        self.assertEqual(broker_response.status_code, status.HTTP_200_OK)
        self.assertEqual(port_response.status_code, status.HTTP_200_OK)
        self.assertEqual(retention_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            PlatformConfig.objects.get(key="mqtt_broker").value,
            "mqtt.example.internal",
        )
        self.assertEqual(PlatformConfig.objects.get(key="mqtt_port").value, 65535)
        self.assertEqual(
            PlatformConfig.objects.get(key="sensor_data_retention_days").value,
            1,
        )

    def test_unknown_custom_key_keeps_json_value_compatibility(self):
        custom_value = {"enabled": True, "thresholds": [1, 2, 3]}

        response = self.client.post(
            reverse("platform-config-list"),
            {
                "key": "custom_plugin_options",
                "value": custom_value,
                "category": "custom",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            PlatformConfig.objects.get(key="custom_plugin_options").value,
            custom_value,
        )


class ConfigureValidationTests(TestCase):
    """configure --set 与 API 对已知数值配置保持对称约束。"""

    initial_values = PlatformConfigValidationApiTests.initial_values

    def setUp(self):
        for key, value in self.initial_values.items():
            meta = get_meta(key)
            PlatformConfig.objects.create(
                key=key,
                value=value,
                category=meta["category"],
                description=meta["description"],
            )

    def _configure(self, *args):
        return call_command(
            "configure",
            *args,
            "--no-reload",
            stdout=StringIO(),
            stderr=StringIO(),
        )

    def test_invalid_cli_values_raise_and_do_not_change_database(self):
        cases = (
            ("mqtt_broker", ""),
            ("mqtt_broker", "   "),
            ("mqtt_port", "true"),
            ("mqtt_port", "0"),
            ("mqtt_port", "65536"),
            ("mqtt_keepalive", "0"),
            ("mqtt_keepalive", "65536"),
            ("device_offline_timeout", "0"),
            ("device_offline_timeout", "86401"),
            ("device_reconnect_attempts", "0"),
            ("device_reconnect_attempts", "101"),
            ("device_reconnect_interval", "0"),
            ("device_reconnect_interval", "3601"),
            ("sensor_data_retention_days", "0"),
            ("sensor_data_retention_days", "3651"),
            ("device_data_retention_days", "0"),
            ("device_data_retention_days", "3651"),
        )

        for key, raw in cases:
            with self.subTest(key=key, value=raw):
                with self.assertRaises(CommandError):
                    self._configure("--set", f"{key}={raw}")
                self.assertEqual(
                    PlatformConfig.objects.get(key=key).value,
                    self.initial_values[key],
                )

    def test_batch_is_validated_before_any_value_is_written(self):
        with self.assertRaises(CommandError):
            self._configure(
                "--set", "mqtt_broker=new-broker.internal",
                "--set", "mqtt_port=0",
            )

        self.assertEqual(
            PlatformConfig.objects.get(key="mqtt_broker").value,
            self.initial_values["mqtt_broker"],
        )
        self.assertEqual(
            PlatformConfig.objects.get(key="mqtt_port").value,
            self.initial_values["mqtt_port"],
        )

    def test_valid_cli_values_use_same_normalization_and_boundaries(self):
        self._configure(
            "--set", "mqtt_broker=  mqtt.example.internal  ",
            "--set", "mqtt_port=65535",
            "--set", "device_data_retention_days=1",
            "--set", "mqtt_username=runner-2",
            "--set", "mqtt_password=new-secret",
        )

        self.assertEqual(
            PlatformConfig.objects.get(key="mqtt_broker").value,
            "mqtt.example.internal",
        )
        self.assertEqual(PlatformConfig.objects.get(key="mqtt_port").value, 65535)
        self.assertEqual(
            PlatformConfig.objects.get(key="device_data_retention_days").value,
            1,
        )
        self.assertEqual(
            PlatformConfig.objects.get(key="mqtt_username").value,
            "runner-2",
        )
        self.assertEqual(
            PlatformConfig.objects.get(key="mqtt_password").value,
            "new-secret",
        )
