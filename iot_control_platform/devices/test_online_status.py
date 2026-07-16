from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from platform_settings.models import PlatformConfig

from .models import Device, DeviceType
from .online_status import clear_device_offline_timeout_cache


class DeviceOnlineTimeoutTests(TestCase):
    def setUp(self):
        self.addCleanup(clear_device_offline_timeout_cache)
        PlatformConfig.objects.create(
            key="device_offline_timeout",
            value=600,
            category="devices",
        )
        clear_device_offline_timeout_cache()
        device_type = DeviceType.objects.create(
            DeviceType_id="online-timeout-type",
            name="在线阈值设备",
        )
        self.device = Device.objects.create(
            device_id="online-timeout-device",
            name="在线阈值设备",
            device_type=device_type,
            last_seen=timezone.now() - timedelta(seconds=400),
            is_online=True,
        )
        self.user = get_user_model().objects.create_user(
            username="online-timeout-user",
            password="test-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_model_and_api_filter_share_runtime_timeout(self):
        self.device.refresh_from_db()
        self.assertTrue(self.device.computed_is_online)

        online = self.client.get(reverse("device-list"), {"online": "true"})
        offline = self.client.get(reverse("device-list"), {"online": "false"})

        online_ids = {row["device_id"] for row in online.data["results"]}
        offline_ids = {row["device_id"] for row in offline.data["results"]}
        self.assertIn(self.device.device_id, online_ids)
        self.assertNotIn(self.device.device_id, offline_ids)

    def test_invalid_timeout_fails_safe_to_default(self):
        PlatformConfig.objects.filter(key="device_offline_timeout").update(value=0)
        clear_device_offline_timeout_cache()
        self.device.last_seen = timezone.now() - timedelta(seconds=400)

        self.assertFalse(self.device.computed_is_online)
