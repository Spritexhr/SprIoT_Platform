from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from devices.models import Device, DeviceType
from sensors.models import Sensor, SensorType

from .consumers import ProjectStreamConsumer
from .models import (
    Project,
    ProjectDeviceMember,
    ProjectSection,
    ProjectSensorMember,
)


class ProjectMembershipRealtimeTests(TestCase):
    """已有 WebSocket 连接应在项目成员变化后立即更新过滤集合和快照。"""

    def setUp(self):
        self.sensor_type = SensorType.objects.create(
            SensorType_id="project-realtime-sensor",
            name="项目实时传感器",
            data_fields=["value"],
        )
        self.device_type = DeviceType.objects.create(
            DeviceType_id="project-realtime-device",
            name="项目实时设备",
            config_parameters=["state"],
        )
        self.project = Project.objects.create(code="RT-MEMBER", name="实时成员测试")
        self.section = ProjectSection.objects.create(
            project=self.project,
            name="实时分区",
        )
        self.first_device = Device.objects.create(
            device_id="RT-DEVICE-1",
            name="实时设备一",
            device_type=self.device_type,
        )
        self.first_member = ProjectDeviceMember.objects.create(
            project=self.project,
            section=self.section,
            device=self.first_device,
        )

    def _consumer(self):
        consumer = ProjectStreamConsumer()
        consumer.scope = {
            "url_route": {"kwargs": {"project_id": self.project.id}},
        }
        consumer.send_json = AsyncMock()
        return consumer

    def test_membership_event_refreshes_existing_connection_filter(self):
        consumer = self._consumer()
        async_to_sync(consumer._send_initial)()
        self.assertEqual(consumer._bound_device_ids, frozenset({"RT-DEVICE-1"}))

        second_device = Device.objects.create(
            device_id="RT-DEVICE-2",
            name="实时设备二",
            device_type=self.device_type,
        )
        ProjectDeviceMember.objects.create(
            project=self.project,
            section=self.section,
            device=second_device,
        )
        async_to_sync(consumer.broadcast_project_membership)({"payload": {}})
        self.assertEqual(
            consumer._bound_device_ids,
            frozenset({"RT-DEVICE-1", "RT-DEVICE-2"}),
        )

        self.first_member.delete()
        async_to_sync(consumer.broadcast_project_membership)({"payload": {}})
        self.assertEqual(consumer._bound_device_ids, frozenset({"RT-DEVICE-2"}))

        consumer.send_json.reset_mock()
        async_to_sync(consumer.broadcast_device_status)(
            {"payload": {"device_id": "RT-DEVICE-1", "status": {"state": "stale"}}}
        )
        consumer.send_json.assert_not_awaited()

        async_to_sync(consumer.broadcast_device_status)(
            {"payload": {"device_id": "RT-DEVICE-2", "status": {"state": "fresh"}}}
        )
        consumer.send_json.assert_awaited_once()
        self.assertEqual(
            consumer.send_json.await_args.args[0]["data"]["device_id"],
            "RT-DEVICE-2",
        )

    def test_device_member_commit_publishes_one_project_refresh(self):
        second_device = Device.objects.create(
            device_id="RT-DEVICE-SIGNAL",
            name="实时信号设备",
            device_type=self.device_type,
        )

        with (
            patch("projects.signals.publish_project_membership_changed") as publish,
            self.captureOnCommitCallbacks(execute=True),
        ):
            ProjectDeviceMember.objects.create(
                project=self.project,
                section=self.section,
                device=second_device,
            )

        publish.assert_called_once_with(self.project.id)

    def test_sensor_member_commit_also_refreshes_snapshot(self):
        sensor = Sensor.objects.create(
            sensor_id="RT-SENSOR-SIGNAL",
            name="实时信号传感器",
            sensor_type=self.sensor_type,
        )

        with (
            patch("projects.signals.publish_project_membership_changed") as publish,
            self.captureOnCommitCallbacks(execute=True),
        ):
            ProjectSensorMember.objects.create(
                project=self.project,
                section=self.section,
                sensor=sensor,
                data_key="value",
            )

        publish.assert_called_once_with(self.project.id)

    def test_moving_member_refreshes_old_and_new_projects(self):
        target_project = Project.objects.create(
            code="RT-MEMBER-NEW",
            name="实时成员新项目",
        )
        target_section = ProjectSection.objects.create(
            project=target_project,
            name="新分区",
        )

        with (
            patch("projects.signals.publish_project_membership_changed") as publish,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.first_member.project = target_project
            self.first_member.section = target_section
            self.first_member.save()

        self.assertEqual(
            {call.args[0] for call in publish.call_args_list},
            {self.project.id, target_project.id},
        )


class ProjectBulkMembershipRealtimeTests(APITestCase):
    """批量导入只广播一次，避免每个连接重复重建大量 snapshot。"""

    def setUp(self):
        staff = get_user_model().objects.create_user(
            username="project-realtime-admin",
            password="test",
            is_staff=True,
        )
        self.client.force_authenticate(staff)
        self.sensor_type = SensorType.objects.create(
            SensorType_id="project-bulk-realtime-sensor",
            name="批量实时传感器",
            data_fields=["value"],
        )
        self.device_type = DeviceType.objects.create(
            DeviceType_id="project-bulk-realtime-device",
            name="批量实时设备",
            config_parameters=["state"],
        )
        self.project = Project.objects.create(code="RT-BULK", name="批量实时测试")
        self.section = ProjectSection.objects.create(
            project=self.project,
            name="批量分区",
        )

    def test_bulk_sensor_import_publishes_one_refresh(self):
        sensors = [
            Sensor.objects.create(
                sensor_id=f"RT-BULK-SENSOR-{index}",
                name=f"批量传感器 {index}",
                sensor_type=self.sensor_type,
            )
            for index in range(2)
        ]

        with (
            patch("projects.views.publish_project_membership_changed") as publish,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("project-sensor-member-list"),
                {
                    "section": self.section.id,
                    "sensor_ids": [sensor.id for sensor in sensors],
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(len(response.data["created"]), 2)
        publish.assert_called_once_with(self.project.id)

    def test_bulk_device_import_publishes_one_refresh(self):
        devices = [
            Device.objects.create(
                device_id=f"RT-BULK-DEVICE-{index}",
                name=f"批量设备 {index}",
                device_type=self.device_type,
            )
            for index in range(2)
        ]

        with (
            patch("projects.views.publish_project_membership_changed") as publish,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("project-device-member-list"),
                {
                    "section": self.section.id,
                    "device_ids": [device.id for device in devices],
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(len(response.data["created"]), 2)
        publish.assert_called_once_with(self.project.id)
