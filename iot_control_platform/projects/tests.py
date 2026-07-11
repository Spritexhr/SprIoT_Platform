import importlib
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from automation.models import ControlScheme
from devices.models import Device, DeviceType
from sensors.models import Sensor, SensorType

from .models import Project, ProjectDeviceMember, ProjectSection, ProjectSensorMember, ProjectView


class ProjectMemberDeleteProtectionTests(APITestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="project-admin", password="test", is_staff=True
        )
        self.viewer = get_user_model().objects.create_user(
            username="project-viewer", password="test"
        )
        self.client.force_authenticate(self.staff)
        sensor_type = SensorType.objects.create(
            SensorType_id="temperature", name="温度", data_fields=["temperature"]
        )
        device_type = DeviceType.objects.create(
            DeviceType_id="valve",
            name="阀门",
            config_parameters=["valve_opening", "power_state"],
        )
        self.sensor = Sensor.objects.create(
            sensor_id="T-1", name="温度一", sensor_type=sensor_type
        )
        self.device = Device.objects.create(
            device_id="V-1", name="阀门一", device_type=device_type
        )
        self.project = Project.objects.create(code="PROTECT", name="删除保护测试")
        self.section = ProjectSection.objects.create(project=self.project, name="一号区域")
        self.sensor_member = ProjectSensorMember.objects.create(
            project=self.project, section=self.section, sensor=self.sensor, tag="T-1"
        )
        self.device_member = ProjectDeviceMember.objects.create(
            project=self.project, section=self.section, device=self.device, tag="V-1"
        )
        self.scheme = ControlScheme.objects.create(
            name="温度闭环",
            project=self.project,
            section=self.section,
            sensor_member=self.sensor_member,
            device_member=self.device_member,
            control_type="pi",
            output_mode="analog",
            is_enabled=False,
            status="idle",
        )

    def assert_protected_response(self, response, resource_type):
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "control_scheme_in_use")
        self.assertEqual(response.data["resource_type"], resource_type)
        self.assertEqual(response.data["blockers"][0]["id"], self.scheme.id)
        self.assertEqual(response.data["blockers"][0]["name"], self.scheme.name)
        self.assertFalse(response.data["blockers"][0]["is_enabled"])

    def test_device_member_returns_409_with_blocking_scheme(self):
        response = self.client.delete(
            reverse("project-device-member-detail", args=[self.device_member.id])
        )
        self.assert_protected_response(response, "device")
        self.assertTrue(ProjectDeviceMember.objects.filter(pk=self.device_member.id).exists())

    def test_sensor_member_returns_409_with_blocking_scheme(self):
        response = self.client.delete(
            reverse("project-sensor-member-detail", args=[self.sensor_member.id])
        )
        self.assert_protected_response(response, "sensor")
        self.assertTrue(ProjectSensorMember.objects.filter(pk=self.sensor_member.id).exists())

    def test_member_can_be_removed_after_scheme_is_deleted(self):
        self.scheme.delete()
        response = self.client.delete(
            reverse("project-device-member-detail", args=[self.device_member.id])
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ProjectDeviceMember.objects.filter(pk=self.device_member.id).exists())
        self.assertTrue(Device.objects.filter(pk=self.device.id).exists())

    def test_viewer_can_read_project_but_cannot_change_configuration(self):
        self.client.force_authenticate(self.viewer)

        detail_response = self.client.get(reverse("project-detail", args=[self.project.id]))
        layout_response = self.client.get(reverse("project-layout", args=[self.project.id]))
        update_response = self.client.patch(
            reverse("project-detail", args=[self.project.id]),
            {"name": "访客不应能修改"},
            format="json",
        )
        section_response = self.client.post(
            reverse("project-section-list"),
            {"project": self.project.id, "name": "访客房间"},
            format="json",
        )

        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(layout_response.status_code, status.HTTP_200_OK)
        self.assertEqual(update_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(section_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_can_change_project_configuration(self):
        response = self.client.patch(
            reverse("project-detail", args=[self.project.id]),
            {"name": "管理人员已修改"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, "管理人员已修改")

    def test_layout_exposes_device_status_fields_for_diagram_binding(self):
        response = self.client.get(reverse("project-layout", args=[self.project.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        device = response.data["sections"][0]["devices"][0]
        self.assertEqual(device["data_fields"], ["valve_opening", "power_state"])

    def test_snapshot_keeps_empty_sensor_timestamp_null_and_offline(self):
        """从未上报数据的传感器不能因生成占位快照而被判为在线。"""
        response = self.client.get(reverse("project-snapshot", args=[self.project.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sample = response.data["samples"][0]
        self.assertIsNone(sample["value"])
        self.assertIsNone(sample["ts"])
        self.assertFalse(sample["is_online"])

    def test_control_scheme_status_is_published_for_diagram_node(self):
        with patch("services.realtime.dispatch.publish_control_scheme") as publish:
            with self.captureOnCommitCallbacks(execute=True):
                self.scheme.is_enabled = True
                self.scheme.status = "running"
                self.scheme.save()

        payload = publish.call_args.args[0]
        self.assertEqual(payload["id"], self.scheme.id)
        self.assertEqual(payload["project"], self.project.id)
        self.assertEqual(payload["section"], self.section.id)
        self.assertEqual(payload["control_type"], "pi")
        self.assertEqual(payload["status"], "running")

    def diagram_config(self):
        return {
            "version": 1,
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "nodes": [
                {
                    "id": "sensor-node",
                    "type": "instrument",
                    "position": {"x": 10, "y": 20},
                    "size": {"w": 186, "h": 102},
                    "binding": {"kind": "sensor", "id": self.sensor_member.point_id},
                    "data": {"label": "T-1"},
                },
                {
                    "id": "device-node",
                    "type": "device_indicator",
                    "position": {"x": 200, "y": 20},
                    "binding": {"kind": "device", "id": self.device.device_id},
                    "data": {"label": "V-1"},
                },
            ],
            "edges": [
                {
                    "id": "edge-1",
                    "source": "sensor-node",
                    "target": "device-node",
                    "sourcePort": "right",
                    "targetPort": "left",
                    "data": {"kind": "signal", "label": ""},
                }
            ],
        }

    def test_diagram_config_accepts_current_room_bindings(self):
        response = self.client.post(
            reverse("project-view-list"),
            {
                "project": self.project.id,
                "section": self.section.id,
                "name": "受校验的 P&ID",
                "view_type": "diagram",
                "config": self.diagram_config(),
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_diagram_config_rejects_missing_edge_endpoint(self):
        config = self.diagram_config()
        config["edges"][0]["target"] = "missing-node"
        response = self.client.post(
            reverse("project-view-list"),
            {
                "project": self.project.id,
                "section": self.section.id,
                "name": "损坏的 P&ID",
                "view_type": "diagram",
                "config": config,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("不存在的节点", str(response.data))

    def test_diagram_config_rejects_binding_outside_section(self):
        config = self.diagram_config()
        config["nodes"][0]["binding"]["id"] = "sensor-outside-room"
        response = self.client.post(
            reverse("project-view-list"),
            {
                "project": self.project.id,
                "section": self.section.id,
                "name": "越界绑定 P&ID",
                "view_type": "diagram",
                "config": config,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("不属于当前房间", str(response.data))

    def test_medium_cleanup_migration_preserves_other_edge_data(self):
        view = ProjectView.objects.create(
            project=self.project,
            section=self.section,
            name="P&ID",
            view_type="diagram",
            config={
                "version": 1,
                "nodes": [],
                "edges": [
                    {"id": "e-1", "data": {"label": "进料", "kind": "process", "medium": "乙苯"}}
                ],
            },
        )
        migration = importlib.import_module("projects.migrations.0004_remove_diagram_edge_medium")
        migration.remove_edge_medium(apps, None)
        view.refresh_from_db()

        self.assertEqual(
            view.config["edges"][0]["data"],
            {"label": "进料", "kind": "process"},
        )
