"""
Sensors模块测试
测试传感器模型和相关功能
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

from sensors.models import SensorType, Sensor, SensorData, SensorStatusCollection


class SensorMqttTopicTest(TestCase):
    """传感器 data / status / control 三主题一致性测试。"""

    def setUp(self):
        self.sensor_type = SensorType.objects.create(
            SensorType_id="mqtt-topic-type",
            name="MQTT 主题测试类型",
            data_fields=["value"],
            config_parameters=["enabled"],
            commands={},
        )

    def test_auto_generate_all_mqtt_topics(self):
        sensor = Sensor.objects.create(
            sensor_id="MQTT-001",
            name="测试传感器",
            sensor_type=self.sensor_type,
        )

        self.assertEqual(sensor.mqtt_topic_data, "iot/sensors/MQTT-001/data")
        self.assertEqual(sensor.mqtt_topic_status, "iot/sensors/MQTT-001/status")
        self.assertEqual(sensor.mqtt_topic_control, "iot/sensors/MQTT-001/control")

    def test_update_sensor_id_regenerates_all_mqtt_topics(self):
        sensor = Sensor.objects.create(
            sensor_id="MQTT-001",
            name="测试传感器",
            sensor_type=self.sensor_type,
        )

        sensor.sensor_id = "MQTT-002"
        sensor.save(update_fields=['sensor_id'])
        sensor.refresh_from_db()

        self.assertEqual(sensor.mqtt_topic_data, "iot/sensors/MQTT-002/data")
        self.assertEqual(sensor.mqtt_topic_status, "iot/sensors/MQTT-002/status")
        self.assertEqual(sensor.mqtt_topic_control, "iot/sensors/MQTT-002/control")

    def test_update_sensor_id_preserves_custom_topics(self):
        sensor = Sensor.objects.create(
            sensor_id="MQTT-CUSTOM-001",
            name="自定义主题传感器",
            sensor_type=self.sensor_type,
            mqtt_topic_data="factory/custom/data",
            mqtt_topic_status="factory/custom/status",
            mqtt_topic_control="factory/custom/control",
        )

        sensor.sensor_id = "MQTT-CUSTOM-002"
        sensor.save(update_fields=['sensor_id'])
        sensor.refresh_from_db()

        self.assertEqual(sensor.mqtt_topic_data, "factory/custom/data")
        self.assertEqual(sensor.mqtt_topic_status, "factory/custom/status")
        self.assertEqual(sensor.mqtt_topic_control, "factory/custom/control")


class SensorTypeTest(TestCase):
    """传感器类型模型测试"""

    def test_create_sensor_type(self):
        """测试创建传感器类型"""
        sensor_type = SensorType.objects.create(
            SensorType_id="DHT11",
            name="DHT11温湿度传感器",
            description="温湿度传感器",
            data_fields=['temperature', 'humidity'],
            config_parameters=['sampling_interval'],
            commands={
                'set_interval': {
                    'mqtt_message': {
                        'command': 'set_interval',
                        'interval': '{value}',
                    },
                    'params': ['value'],
                }
            },
        )

        self.assertEqual(sensor_type.SensorType_id, "DHT11")
        self.assertEqual(sensor_type.name, "DHT11温湿度传感器")
        self.assertEqual(sensor_type.data_fields, ['temperature', 'humidity'])
        self.assertEqual(sensor_type.config_parameters, ['sampling_interval'])
        self.assertEqual(
            sensor_type.commands['set_interval']['mqtt_message']['command'],
            'set_interval',
        )

    def test_sensor_type_str(self):
        """测试字符串表示"""
        sensor_type = SensorType.objects.create(
            SensorType_id="TEST-TYPE",
            name="测试传感器",
        )
        self.assertEqual(str(sensor_type), "测试传感器")


class SensorModelTest(TestCase):
    """传感器模型测试"""

    def setUp(self):
        """创建测试数据"""
        self.sensor_type = SensorType.objects.create(
            SensorType_id="DHT11",
            name="DHT11",
            data_fields=['temperature', 'humidity'],
            config_parameters=['sampling_interval'],
            commands={},
        )

    def test_create_sensor(self):
        """测试创建传感器"""
        sensor = Sensor.objects.create(
            sensor_id="DHT11-001",
            name="客厅温湿度传感器",
            sensor_type=self.sensor_type,
            location="客厅",
        )

        self.assertEqual(sensor.sensor_id, "DHT11-001")
        self.assertEqual(sensor.name, "客厅温湿度传感器")
        self.assertEqual(sensor.sensor_type, self.sensor_type)
        self.assertEqual(sensor.location, "客厅")
        self.assertFalse(sensor.is_online)
        self.assertIsNone(sensor.last_seen)

    def test_auto_generate_mqtt_topics(self):
        """测试自动生成MQTT主题"""
        sensor = Sensor.objects.create(
            sensor_id="DHT11-001",
            name="测试传感器",
            sensor_type=self.sensor_type
        )

        self.assertEqual(sensor.mqtt_topic_data, "iot/sensors/DHT11-001/data")
        self.assertEqual(sensor.mqtt_topic_status, "iot/sensors/DHT11-001/status")
        self.assertEqual(sensor.mqtt_topic_control, "iot/sensors/DHT11-001/control")

    def test_sensor_uses_type_level_data_and_config_definitions(self):
        """数据字段和配置参数只保存在类型上，传感器通过外键读取。"""
        sensor = Sensor.objects.create(
            sensor_id="DHT11-001",
            name="测试传感器",
            sensor_type=self.sensor_type
        )

        self.assertEqual(sensor.sensor_type.data_fields, ['temperature', 'humidity'])
        self.assertEqual(sensor.sensor_type.config_parameters, ['sampling_interval'])

    def test_update_last_seen_marks_online_and_never_moves_backwards(self):
        """服务端心跳会标记在线，较旧时间不能覆盖新心跳。"""
        sensor = Sensor.objects.create(
            sensor_id="DHT11-001",
            name="测试传感器",
            sensor_type=self.sensor_type
        )

        latest = timezone.now() - timedelta(seconds=30)
        sensor.update_last_seen(latest)
        sensor.refresh_from_db()
        self.assertEqual(sensor.last_seen, latest)
        self.assertTrue(sensor.is_online)

        sensor.update_last_seen(latest - timedelta(minutes=1))
        sensor.refresh_from_db()
        self.assertEqual(sensor.last_seen, latest)

    def test_stalled_older_heartbeat_cannot_regress_concurrent_new_value(self):
        sensor = Sensor.objects.create(
            sensor_id="DHT11-RACE",
            name="并发心跳传感器",
            sensor_type=self.sensor_type,
        )
        committed = timezone.now()
        Sensor.objects.filter(pk=sensor.pk).update(last_seen=committed)
        sensor.last_seen = committed
        stalled_started_at = committed - timedelta(minutes=1)

        with patch("sensors.models.timezone.now", return_value=stalled_started_at):
            sensor.update_last_seen(stalled_started_at)

        sensor.refresh_from_db()
        self.assertEqual(sensor.last_seen, committed)
        self.assertTrue(sensor.is_online)

    def test_future_legacy_last_seen_is_offline_and_migration_clears_it(self):
        sensor = Sensor.objects.create(
            sensor_id="DHT11-FUTURE",
            name="未来脏心跳传感器",
            sensor_type=self.sensor_type,
        )
        Sensor.objects.filter(pk=sensor.pk).update(
            last_seen=timezone.now() + timedelta(days=365),
            is_online=True,
        )
        sensor.refresh_from_db()
        self.assertFalse(sensor.computed_is_online)

        migration = importlib.import_module(
            "sensors.migrations.0009_remove_sensordata_sensors_sen_timesta_a94ae6_idx_and_more"
        )
        migration.clear_future_last_seen(apps, None)
        sensor.refresh_from_db()
        self.assertIsNone(sensor.last_seen)
        self.assertFalse(sensor.is_online)

    def test_computed_online_status_online(self):
        """测试在线状态检查（在线）"""
        sensor = Sensor.objects.create(
            sensor_id="DHT11-001",
            name="测试传感器",
            sensor_type=self.sensor_type,
        )

        # 设置最近上报时间
        sensor.last_seen = timezone.now() - timedelta(seconds=30)
        self.assertTrue(sensor.computed_is_online)

    def test_computed_online_status_offline_does_not_mutate_persisted_state(self):
        """测试在线状态检查（离线）"""
        sensor = Sensor.objects.create(
            sensor_id="DHT11-001",
            name="测试传感器",
            sensor_type=self.sensor_type,
        )

        # 设置很久之前的上报时间
        sensor.last_seen = timezone.now() - timedelta(minutes=4)
        sensor.is_online = True
        sensor.save(update_fields=['last_seen', 'is_online'])

        self.assertFalse(sensor.computed_is_online)
        sensor.refresh_from_db()
        self.assertTrue(sensor.is_online)

    def test_sensor_without_last_seen_is_computed_offline(self):
        """从未上报过的传感器应实时判定为离线。"""
        sensor = Sensor.objects.create(
            sensor_id="DHT11-001",
            name="测试传感器",
            sensor_type=self.sensor_type
        )

        self.assertFalse(sensor.computed_is_online)

    def test_related_data_records_can_be_time_filtered(self):
        """当前模型通过反向关系统计指定时间范围内的原始数据。"""
        sensor = Sensor.objects.create(
            sensor_id="DHT11-001",
            name="测试传感器",
            sensor_type=self.sensor_type
        )
        
        now = timezone.now()
        for i in range(5):
            SensorData.objects.create(
                sensor=sensor,
                data={'temperature': 25.0 + i},
                timestamp=now - timedelta(hours=i)
            )
        SensorData.objects.create(
            sensor=sensor,
            data={'temperature': 99.0},
            timestamp=now - timedelta(hours=25),
        )

        count = sensor.data_records.filter(
            timestamp__gte=now - timedelta(hours=24),
        ).count()
        self.assertEqual(count, 5)

    def test_sensor_str(self):
        """测试字符串表示"""
        sensor = Sensor.objects.create(
            sensor_id="DHT11-001",
            name="客厅传感器",
            sensor_type=self.sensor_type
        )

        self.assertEqual(str(sensor), "客厅传感器 (DHT11-001)")


class SensorDataTest(TestCase):
    """传感器数据模型测试"""

    def setUp(self):
        """创建测试传感器"""
        sensor_type = SensorType.objects.create(
            SensorType_id="DHT11",
            name="DHT11",
            data_fields=['temperature', 'humidity'],
            config_parameters=[],
            commands={},
        )

        self.sensor = Sensor.objects.create(
            sensor_id="DHT11-001",
            name="测试传感器",
            sensor_type=sensor_type
        )

    def test_create_sensor_data(self):
        """测试创建传感器数据"""
        timestamp = timezone.now()
        data = SensorData.objects.create(
            sensor=self.sensor,
            data={'temperature': 25.5, 'humidity': 60.0},
            timestamp=timestamp,
        )

        self.assertEqual(data.sensor, self.sensor)
        self.assertEqual(data.data, {'temperature': 25.5, 'humidity': 60.0})
        self.assertEqual(data.timestamp, timestamp)
        self.assertIsNotNone(data.received_at)

    def test_arbitrary_payload_fields_remain_in_json(self):
        """业务字段保持在 JSON 载荷中，不再复制为主模型字段。"""
        payload = {
            'temperature': 26.5,
            'humidity': 65.0,
            'pressure': 1013.25,
            'custom_metric': {'value': 7},
        }
        data = SensorData.objects.create(
            sensor=self.sensor,
            data=payload,
            timestamp=timezone.now()
        )

        data.refresh_from_db()
        self.assertEqual(data.data, payload)

    def test_save_marks_sensor_online_using_server_receive_time(self):
        """设备时间原样保存，但在线心跳必须采用服务端接收时间。"""
        device_timestamp = timezone.now() - timedelta(days=365)
        before_save = timezone.now()

        data = SensorData.objects.create(
            sensor=self.sensor,
            data={'temperature': 27.0, 'humidity': 70.0},
            timestamp=device_timestamp,
        )
        after_save = timezone.now()
        self.sensor.refresh_from_db()

        self.assertEqual(data.timestamp, device_timestamp)
        self.assertTrue(self.sensor.is_online)
        self.assertGreaterEqual(self.sensor.last_seen, before_save)
        self.assertLessEqual(self.sensor.last_seen, after_save)

    def test_time_range_filtering_uses_record_timestamp(self):
        """历史统计按记录时间过滤，载荷仍以原始 JSON 返回。"""
        now = timezone.now()

        for i in range(10):
            SensorData.objects.create(
                sensor=self.sensor,
                data={'temperature': 20.0 + i, 'humidity': 50.0 + i},
                timestamp=now - timedelta(hours=i)
            )
        SensorData.objects.create(
            sensor=self.sensor,
            data={'temperature': -99.0, 'humidity': 0.0},
            timestamp=now - timedelta(hours=25),
        )

        records = SensorData.objects.filter(
            sensor=self.sensor,
            timestamp__gte=now - timedelta(hours=24),
            timestamp__lte=now,
        )

        self.assertEqual(records.count(), 10)
        self.assertEqual(records.first().data['temperature'], 20.0)


class SensorApiHistoryQueryTests(APITestCase):
    """列表只取最新值，历史接口有严格边界且查询数固定。"""

    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username='sensor-api-admin', password='test-password', is_staff=True
        )
        self.client.force_authenticate(self.staff)
        self.sensor_type = SensorType.objects.create(
            SensorType_id='query-test-type',
            name='查询测试传感器',
            data_fields=['value'],
            config_parameters=[],
            commands={
                'refresh': {
                    'mqtt_message': {'command': 'refresh'},
                    'description': '刷新',
                    'params': [],
                }
            },
        )
        now = timezone.now()
        self.sensors = []
        for index in range(4):
            sensor = Sensor.objects.create(
                sensor_id=f'QUERY-SENSOR-{index}',
                name=f'查询传感器 {index}',
                sensor_type=self.sensor_type,
            )
            SensorData.objects.create(
                sensor=sensor,
                data={'value': f'old-{index}'},
                timestamp=now - timedelta(minutes=5),
            )
            SensorData.objects.create(
                sensor=sensor,
                data={'value': f'future-poison-{index}'},
                timestamp=now + timedelta(days=3650),
            )
            SensorData.objects.create(
                sensor=sensor,
                data={'value': f'latest-{index}'},
                timestamp=now,
            )
            SensorStatusCollection.objects.create(
                sensor=sensor,
                data={'state': 'old'},
                event_name='old',
                timestamp=now - timedelta(minutes=5),
            )
            SensorStatusCollection.objects.create(
                sensor=sensor,
                data={'state': 'latest'},
                event_name='latest',
                timestamp=now,
            )
            self.sensors.append(sensor)
        self.empty_sensor = Sensor.objects.create(
            sensor_id='QUERY-SENSOR-EMPTY',
            name='无历史传感器',
            sensor_type=self.sensor_type,
        )
        Sensor.objects.filter(pk=self.empty_sensor.pk).update(
            last_seen=now + timedelta(days=365),
            is_online=True,
        )
        self.empty_sensor.refresh_from_db()

    def test_list_and_detail_use_fixed_query_count_with_latest_data(self):
        with self.assertNumQueries(2):
            list_response = self.client.get(reverse('sensor-list'))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        rows = list_response.data['results']
        first = next(row for row in rows if row['sensor_id'] == self.sensors[0].sensor_id)
        empty = next(row for row in rows if row['sensor_id'] == self.empty_sensor.sensor_id)
        self.assertEqual(first['latest_data']['data'], {'value': 'latest-0'})
        self.assertIsNone(empty['latest_data'])
        self.assertFalse(empty['is_online'])

        online_response = self.client.get(reverse('sensor-list'), {'online': 'true'})
        offline_response = self.client.get(reverse('sensor-list'), {'online': 'false'})
        online_ids = {row['sensor_id'] for row in online_response.data['results']}
        offline_ids = {row['sensor_id'] for row in offline_response.data['results']}
        self.assertNotIn(self.empty_sensor.sensor_id, online_ids)
        self.assertIn(self.empty_sensor.sensor_id, offline_ids)

        with self.assertNumQueries(1):
            detail_response = self.client.get(
                reverse('sensor-detail', args=[self.sensors[0].sensor_id])
            )

        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data['latest_data']['data'], {'value': 'latest-0'})
        self.assertEqual(detail_response.data['data_count_24h'], 3)

    @patch(
        'services.sensors_service.sensor_command_send_service.'
        'sensor_command_send_service.send_command',
        return_value=True,
    )
    def test_command_lookup_does_not_load_history(self, send_command):
        with self.assertNumQueries(1):
            response = self.client.post(
                reverse('sensor-send-command', args=[self.sensors[0].sensor_id]),
                {'command_name': 'refresh'},
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        send_command.assert_called_once()

    @patch(
        'services.sensors_service.sensor_command_send_service.'
        'sensor_command_send_service.send_command_with_make_sure',
    )
    @patch(
        'services.sensors_service.sensor_command_send_service.'
        'sensor_command_send_service.send_command',
        return_value=True,
    )
    def test_command_strictly_parses_boolean_and_params(self, send_command, confirmed):
        url = reverse('sensor-send-command', args=[self.sensors[0].sensor_id])

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
            {'command_name': ['refresh']},
            format='json',
        )

        self.assertEqual(false_response.status_code, status.HTTP_200_OK)
        send_command.assert_called_once()
        confirmed.assert_not_called()
        self.assertEqual(invalid_bool.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_params.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_name.status_code, status.HTTP_400_BAD_REQUEST)

    def test_history_actions_have_fixed_query_count_and_keep_response_shape(self):
        with self.assertNumQueries(2):
            data_response = self.client.get(
                reverse('sensor-sensor-data', args=[self.sensors[0].sensor_id]),
                {'hours': '168', 'limit': '1'},
            )
        with self.assertNumQueries(2):
            status_response = self.client.get(
                reverse('sensor-sensor-status', args=[self.sensors[0].sensor_id]),
                {'limit': '1'},
            )

        self.assertEqual(data_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(data_response.data), 1)
        self.assertEqual(data_response.data[0]['data'], {'value': 'latest-0'})
        self.assertEqual(status_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(status_response.data), 1)
        self.assertEqual(status_response.data[0]['event_name'], 'latest')

    def test_history_query_params_reject_invalid_or_unbounded_values(self):
        data_url = reverse('sensor-sensor-data', args=[self.sensors[0].sensor_id])
        status_url = reverse('sensor-sensor-status', args=[self.sensors[0].sensor_id])
        invalid_data_params = (
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
        for params in invalid_data_params:
            with self.subTest(params=params):
                response = self.client.get(data_url, params)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        for invalid_limit in ('0', '-1', '2001', 'many', ''):
            with self.subTest(status_limit=invalid_limit):
                response = self.client.get(status_url, {'limit': invalid_limit})
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
