"""
Devices模块测试
测试设备模型和相关功能
参考 sensors 结构
"""
import importlib

from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from rest_framework import status
from rest_framework.test import APITestCase

from devices.models import DeviceType, Device, DeviceStatusCollection
from devices.online_status import get_device_offline_timeout


class DeviceTypeTest(TestCase):
    """设备类型模型测试"""

    def test_create_device_type(self):
        """测试创建设备类型"""
        device_type = DeviceType.objects.create(
            DeviceType_id="LED-01",
            name="LED灯",
            description="智能LED灯",
            config_parameters=['power_state', 'brightness', 'heartbeat_interval']
        )

        self.assertEqual(device_type.name, "LED灯")
        self.assertIn('power_state', device_type.config_parameters)

    def test_device_type_str(self):
        """测试字符串表示"""
        device_type = DeviceType.objects.create(
            DeviceType_id="TEST-01",
            name="测试设备"
        )
        self.assertEqual(str(device_type), "测试设备")


class DeviceModelTest(TestCase):
    """设备模型测试"""

    def setUp(self):
        """创建测试数据"""
        self.device_type = DeviceType.objects.create(
            DeviceType_id="LED-01",
            name="智能灯",
            config_parameters=['power_state', 'brightness', 'heartbeat_interval']
        )

    def test_create_device(self):
        """测试创建设备"""
        device = Device.objects.create(
            device_id="LED-001",
            name="客厅灯",
            device_type=self.device_type,
            location="客厅"
        )

        self.assertEqual(device.device_id, "LED-001")
        self.assertEqual(device.name, "客厅灯")
        self.assertFalse(device.is_online)

    def test_auto_generate_mqtt_topics(self):
        """测试自动生成MQTT主题"""
        device = Device.objects.create(
            device_id="LED-001",
            name="测试灯",
            device_type=self.device_type
        )

        self.assertEqual(device.mqtt_topic_data, "iot/devices/LED-001/status")
        self.assertEqual(device.mqtt_topic_control, "iot/devices/LED-001/control")

    def test_check_online_status_online(self):
        """测试在线状态检查（在线）"""
        device = Device.objects.create(
            device_id="LED-001",
            name="测试灯",
            device_type=self.device_type
        )

        device.last_seen = timezone.now() - timedelta(seconds=60)
        device.is_online = True
        device.save()

        self.assertTrue(device.check_online_status())

    def test_check_online_status_offline(self):
        """测试在线状态检查（离线）"""
        device = Device.objects.create(
            device_id="LED-001",
            name="测试灯",
            device_type=self.device_type
        )

        device.last_seen = timezone.now() - timedelta(seconds=400)
        device.is_online = True
        device.save()

        self.assertFalse(device.check_online_status())
        device.refresh_from_db()
        self.assertFalse(device.is_online)

    def test_update_heartbeat(self):
        """测试更新心跳"""
        device = Device.objects.create(
            device_id="LED-001",
            name="测试灯",
            device_type=self.device_type,
            is_online=False
        )

        device.update_heartbeat()

        device.refresh_from_db()
        self.assertTrue(device.is_online)
        self.assertIsNotNone(device.last_seen)

    def test_stalled_older_heartbeat_cannot_regress_concurrent_new_value(self):
        device = Device.objects.create(
            device_id="LED-RACE",
            name="并发心跳设备",
            device_type=self.device_type,
        )
        committed = timezone.now()
        Device.objects.filter(pk=device.pk).update(last_seen=committed)
        device.last_seen = committed
        stalled_started_at = committed - timedelta(minutes=1)

        with patch("devices.models.timezone.now", return_value=stalled_started_at):
            device.update_heartbeat(stalled_started_at)

        device.refresh_from_db()
        self.assertEqual(device.last_seen, committed)

    def test_future_legacy_last_seen_is_offline_and_migration_clears_it(self):
        device = Device.objects.create(
            device_id="LED-FUTURE",
            name="未来脏心跳设备",
            device_type=self.device_type,
        )
        Device.objects.filter(pk=device.pk).update(
            last_seen=timezone.now() + timedelta(days=365),
            is_online=True,
        )
        device.refresh_from_db()
        self.assertFalse(device.computed_is_online)

        migration = importlib.import_module(
            "devices.migrations.0014_remove_devicestatuscollection_devices_dev_timesta_e69fed_idx_and_more"
        )
        migration.clear_future_last_seen(apps, None)
        device.refresh_from_db()
        self.assertIsNone(device.last_seen)
        self.assertFalse(device.is_online)

    def test_device_str(self):
        """测试字符串表示"""
        device = Device.objects.create(
            device_id="LED-001",
            name="客厅灯",
            device_type=self.device_type
        )

        self.assertEqual(str(device), "客厅灯 (LED-001)")


class DeviceStatusCollectionTest(TestCase):
    """设备状态记录测试"""

    def setUp(self):
        """创建测试设备"""
        device_type = DeviceType.objects.create(
            DeviceType_id="LED-01",
            name="智能灯"
        )
        self.device = Device.objects.create(
            device_id="LED-001",
            name="测试灯",
            device_type=device_type
        )

    def test_create_device_status(self):
        """测试创建设备状态记录"""
        record = DeviceStatusCollection.objects.create(
            device=self.device,
            data={'power_state': True, 'brightness': 80},
            event_name='current_status',
            timestamp=timezone.now()
        )

        self.assertEqual(record.device, self.device)
        self.assertEqual(record.data['power_state'], True)
        self.assertEqual(record.data['brightness'], 80)
        self.assertEqual(record.event_name, 'current_status')


class DeviceApiHistoryQueryTests(APITestCase):
    """列表只取最新状态，历史接口有严格边界且查询数固定。"""

    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username='device-api-admin', password='test-password', is_staff=True
        )
        self.client.force_authenticate(self.staff)
        self.device_type = DeviceType.objects.create(
            DeviceType_id='query-test-type',
            name='查询测试设备',
            config_parameters=['state'],
            commands={
                'refresh': {
                    'mqtt_message': {'command': 'refresh'},
                    'description': '刷新',
                    'params': [],
                }
            },
        )
        now = timezone.now()
        self.devices = []
        for index in range(4):
            device = Device.objects.create(
                device_id=f'QUERY-DEVICE-{index}',
                name=f'查询设备 {index}',
                device_type=self.device_type,
            )
            DeviceStatusCollection.objects.create(
                device=device,
                data={'state': f'old-{index}'},
                event_name='old',
                timestamp=now - timedelta(minutes=5),
            )
            DeviceStatusCollection.objects.create(
                device=device,
                data={'state': f'future-poison-{index}'},
                event_name='future-poison',
                timestamp=now + timedelta(days=3650),
            )
            DeviceStatusCollection.objects.create(
                device=device,
                data={'state': f'latest-{index}'},
                event_name='latest',
                timestamp=now,
            )
            self.devices.append(device)
        self.empty_device = Device.objects.create(
            device_id='QUERY-DEVICE-EMPTY',
            name='无历史设备',
            device_type=self.device_type,
        )
        Device.objects.filter(pk=self.empty_device.pk).update(
            last_seen=now + timedelta(days=365),
            is_online=True,
        )
        self.empty_device.refresh_from_db()

    def test_list_and_detail_use_fixed_query_count_with_latest_status(self):
        # 在线阈值使用短 TTL 配置缓存；预热后验证资源数量不会增加查询。
        get_device_offline_timeout()
        with self.assertNumQueries(2):
            list_response = self.client.get(reverse('device-list'))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        rows = list_response.data['results']
        first = next(row for row in rows if row['device_id'] == self.devices[0].device_id)
        empty = next(row for row in rows if row['device_id'] == self.empty_device.device_id)
        self.assertEqual(first['latest_data']['data'], {'state': 'latest-0'})
        self.assertEqual(first['latest_data']['event_name'], 'latest')
        self.assertIsNone(empty['latest_data'])
        self.assertFalse(empty['is_online'])

        online_response = self.client.get(reverse('device-list'), {'online': 'true'})
        offline_response = self.client.get(reverse('device-list'), {'online': 'false'})
        online_ids = {row['device_id'] for row in online_response.data['results']}
        offline_ids = {row['device_id'] for row in offline_response.data['results']}
        self.assertNotIn(self.empty_device.device_id, online_ids)
        self.assertIn(self.empty_device.device_id, offline_ids)

        with self.assertNumQueries(1):
            detail_response = self.client.get(
                reverse('device-detail', args=[self.devices[0].device_id])
            )

        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data['latest_data']['data'], {'state': 'latest-0'})
        self.assertEqual(detail_response.data['data_count_24h'], 3)

    @patch(
        'services.devices_service.device_command_send_service.'
        'device_command_send_service.send_command',
        return_value=True,
    )
    def test_command_lookup_does_not_load_history(self, send_command):
        with self.assertNumQueries(1):
            response = self.client.post(
                reverse('device-send-command', args=[self.devices[0].device_id]),
                {'command_name': 'refresh'},
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        send_command.assert_called_once()

    @patch(
        'services.devices_service.device_command_send_service.'
        'device_command_send_service.send_command_with_make_sure',
    )
    @patch(
        'services.devices_service.device_command_send_service.'
        'device_command_send_service.send_command',
        return_value=True,
    )
    def test_command_strictly_parses_boolean_and_params(self, send_command, confirmed):
        url = reverse('device-send-command', args=[self.devices[0].device_id])

        false_response = self.client.post(
            url,
            {'command_name': 'refresh', 'make_sure': 'false', 'params': {}},
            format='json',
        )
        invalid_bool = self.client.post(
            url,
            {'command_name': 'refresh', 'make_sure': 'yes'},
            format='json',
        )
        invalid_params = self.client.post(
            url,
            {'command_name': 'refresh', 'params': ['not', 'an', 'object']},
            format='json',
        )
        invalid_name = self.client.post(
            url,
            {'command_name': {'name': 'refresh'}},
            format='json',
        )

        self.assertEqual(false_response.status_code, status.HTTP_200_OK)
        send_command.assert_called_once()
        confirmed.assert_not_called()
        self.assertEqual(invalid_bool.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_params.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_name.status_code, status.HTTP_400_BAD_REQUEST)

    def test_history_action_has_fixed_query_count_and_keeps_response_shape(self):
        with self.assertNumQueries(2):
            response = self.client.get(
                reverse('device-device-status', args=[self.devices[0].device_id]),
                {'hours': '168', 'limit': '1'},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['data'], {'state': 'latest-0'})
        self.assertEqual(response.data[0]['event_name'], 'latest')

    def test_history_query_params_reject_invalid_or_unbounded_values(self):
        url = reverse('device-device-status', args=[self.devices[0].device_id])
        invalid_params = (
            {'hours': '0'},
            {'hours': '-1'},
            {'hours': '745'},
            {'hours': '1.5'},
            {'hours': ''},
            {'limit': '0'},
            {'limit': '-1'},
            {'limit': '2001'},
            {'limit': 'many'},
        )
        for params in invalid_params:
            with self.subTest(params=params):
                response = self.client.get(url, params)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
