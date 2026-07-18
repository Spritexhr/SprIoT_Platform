from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from devices.models import Device, DeviceStatusCollection, DeviceType
from platform_settings.models import PlatformConfig
from sensors.models import Sensor, SensorData, SensorStatusCollection, SensorType


class CleanupOldDataCommandTests(TestCase):
    def setUp(self):
        sensor_type = SensorType.objects.create(
            SensorType_id="retention-sensor-type",
            name="留存传感器类型",
        )
        self.sensor = Sensor.objects.create(
            sensor_id="retention-sensor",
            name="留存传感器",
            sensor_type=sensor_type,
        )
        device_type = DeviceType.objects.create(
            DeviceType_id="retention-device-type",
            name="留存设备类型",
        )
        self.device = Device.objects.create(
            device_id="retention-device",
            name="留存设备",
            device_type=device_type,
        )
        PlatformConfig.objects.create(
            key="sensor_data_retention_days", value=30, category="data_retention"
        )
        PlatformConfig.objects.create(
            key="device_data_retention_days", value=30, category="data_retention"
        )

    def _create_records(self):
        old = timezone.now() - timedelta(days=31)
        recent = timezone.now() - timedelta(days=1)
        for timestamp in (old, recent):
            sensor_data = SensorData.objects.create(
                sensor=self.sensor, data={"value": 1}, timestamp=timestamp
            )
            sensor_status = SensorStatusCollection.objects.create(
                sensor=self.sensor,
                data={"enabled": True},
                event_name="status",
                timestamp=timestamp,
            )
            device_status = DeviceStatusCollection.objects.create(
                device=self.device,
                data={"enabled": True},
                event_name="status",
                timestamp=timestamp,
            )
            SensorData.objects.filter(pk=sensor_data.pk).update(received_at=timestamp)
            SensorStatusCollection.objects.filter(pk=sensor_status.pk).update(
                received_at=timestamp
            )
            DeviceStatusCollection.objects.filter(pk=device_status.pk).update(
                received_at=timestamp
            )

    def test_cleanup_covers_sensor_data_sensor_status_and_device_status(self):
        self._create_records()

        call_command("cleanup_old_data", batch_size=1, stdout=StringIO())

        self.assertEqual(SensorData.objects.count(), 1)
        self.assertEqual(SensorStatusCollection.objects.count(), 1)
        self.assertEqual(DeviceStatusCollection.objects.count(), 1)

    def test_dry_run_does_not_delete_records(self):
        self._create_records()

        call_command("cleanup_old_data", dry_run=True, stdout=StringIO())

        self.assertEqual(SensorData.objects.count(), 2)
        self.assertEqual(SensorStatusCollection.objects.count(), 2)
        self.assertEqual(DeviceStatusCollection.objects.count(), 2)

    def test_cleanup_uses_server_received_time_not_untrusted_device_time(self):
        now = timezone.now()
        future_device_time = now + timedelta(days=365)
        old_received_time = now - timedelta(days=31)
        record = SensorData.objects.create(
            sensor=self.sensor,
            data={"value": 1},
            timestamp=future_device_time,
        )
        SensorData.objects.filter(pk=record.pk).update(
            received_at=old_received_time
        )

        call_command("cleanup_old_data", stdout=StringIO())

        self.assertFalse(SensorData.objects.filter(pk=record.pk).exists())

    def test_rejects_dangerous_retention_and_batch_values(self):
        PlatformConfig.objects.filter(key="sensor_data_retention_days").update(value=0)
        with self.assertRaises(CommandError):
            call_command("cleanup_old_data", stdout=StringIO())

        PlatformConfig.objects.filter(key="sensor_data_retention_days").update(value=30)
        with self.assertRaises(CommandError):
            call_command("cleanup_old_data", batch_size=0, stdout=StringIO())


class CleanupOldDataApiTests(APITestCase):
    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser(
            username="cleanup-root",
            password="test-password-123",
            email="cleanup@example.com",
        )
        self.client.force_authenticate(self.superuser)
        sensor_type = SensorType.objects.create(
            SensorType_id="cleanup-api-sensor-type",
            name="清理 API 传感器类型",
        )
        self.sensor = Sensor.objects.create(
            sensor_id="cleanup-api-sensor",
            name="清理 API 传感器",
            sensor_type=sensor_type,
        )
        PlatformConfig.objects.create(
            key="sensor_data_retention_days",
            value=30,
            category="data_retention",
        )
        PlatformConfig.objects.create(
            key="device_data_retention_days",
            value=30,
            category="data_retention",
        )
        self.url = reverse("platform-config-cleanup-old-data")

    def _create_expired_record(self):
        old = timezone.now() - timedelta(days=31)
        record = SensorData.objects.create(
            sensor=self.sensor,
            data={"value": 1},
            timestamp=old,
        )
        SensorData.objects.filter(pk=record.pk).update(received_at=old)
        return record

    def test_cleanup_api_defaults_to_dry_run(self):
        record = self._create_expired_record()

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["dry_run"])
        self.assertEqual(response.data["message"], "cleanup preview completed")
        self.assertTrue(SensorData.objects.filter(pk=record.pk).exists())

    def test_cleanup_api_rejects_non_boolean_dry_run(self):
        record = self._create_expired_record()

        response = self.client.post(
            self.url,
            {
                "dry_run": "false",
                "confirmation": "DELETE_EXPIRED_HISTORY",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("dry_run", response.data)
        self.assertTrue(SensorData.objects.filter(pk=record.pk).exists())

    def test_cleanup_api_requires_exact_confirmation_for_real_delete(self):
        record = self._create_expired_record()

        response = self.client.post(
            self.url,
            {"dry_run": False, "confirmation": "DELETE_HISTORY"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("confirmation", response.data)
        self.assertTrue(SensorData.objects.filter(pk=record.pk).exists())

    def test_cleanup_api_deletes_only_with_exact_confirmation(self):
        record = self._create_expired_record()

        response = self.client.post(
            self.url,
            {
                "dry_run": False,
                "confirmation": "DELETE_EXPIRED_HISTORY",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["dry_run"])
        self.assertEqual(response.data["message"], "cleanup completed")
        self.assertFalse(SensorData.objects.filter(pk=record.pk).exists())
