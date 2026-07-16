import importlib
from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from automation.models import ControlScheme
from devices.models import Device, DeviceStatusCollection, DeviceType
from sensors.models import Sensor, SensorData, SensorType

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

    def test_series_is_bounded_stable_and_project_scoped(self):
        now = timezone.now()
        for index in range(3):
            SensorData.objects.create(
                sensor=self.sensor,
                data={"temperature": index},
                timestamp=now,
            )
        url = reverse("project-series", args=[self.project.id])

        response = self.client.get(
            url,
            {"kind": "sensor", "source_id": self.sensor.sensor_id, "limit": "2"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["count"], 3)
        self.assertTrue(response.data["truncated"])
        self.assertEqual(
            [point["data"]["temperature"] for point in response.data["points"]],
            [1, 2],
        )

        invalid_queries = (
            {"kind": "sensor", "source_id": self.sensor.sensor_id, "start": "bad-date"},
            {"kind": "sensor", "source_id": self.sensor.sensor_id, "limit": "0"},
            {"kind": "sensor", "source_id": self.sensor.sensor_id, "limit": "10001"},
            {"kind": "sensor", "source_id": self.sensor.sensor_id, "limit": "1.5"},
            {
                "kind": "sensor",
                "source_id": self.sensor.sensor_id,
                "start": (now - timedelta(days=32)).isoformat(),
                "end": now.isoformat(),
            },
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                invalid = self.client.get(url, query)
                self.assertEqual(
                    invalid.status_code,
                    status.HTTP_400_BAD_REQUEST,
                    invalid.data,
                )

        unbound = Sensor.objects.create(
            sensor_id="T-UNBOUND",
            name="未加入项目的温度",
            sensor_type=self.sensor.sensor_type,
        )
        unbound_response = self.client.get(
            url,
            {"kind": "sensor", "source_id": unbound.sensor_id},
        )
        missing_project_response = self.client.get(
            reverse("project-series", args=[self.project.id + 9999]),
            {"kind": "sensor", "source_id": self.sensor.sensor_id},
        )
        self.assertEqual(unbound_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            missing_project_response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

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


class ProjectQueryPerformanceTests(APITestCase):
    """项目聚合接口的查询数不能随项目或成员数量线性增长。"""

    def setUp(self):
        self.viewer = get_user_model().objects.create_user(
            username="project-query-viewer",
            password="test",
        )
        self.client.force_authenticate(self.viewer)
        self.sensor_type = SensorType.objects.create(
            SensorType_id="project-query-sensor",
            name="项目查询传感器",
            data_fields=["value"],
            config_parameters=[],
            commands={},
        )
        self.device_type = DeviceType.objects.create(
            DeviceType_id="project-query-device",
            name="项目查询设备",
            config_parameters=["state"],
            commands={"refresh": {"mqtt_message": {"command": "refresh"}}},
        )
        self.project = Project.objects.create(code="QUERY", name="查询性能项目")
        self.section = ProjectSection.objects.create(
            project=self.project,
            name="查询性能分区",
        )
        ProjectView.objects.create(
            project=self.project,
            section=self.section,
            name="默认卡片",
            view_type="card",
        )

        now = timezone.now()
        self.sensors = []
        self.devices = []
        for index in range(5):
            sensor = Sensor.objects.create(
                sensor_id=f"PROJECT-SENSOR-{index}",
                name=f"项目传感器 {index}",
                sensor_type=self.sensor_type,
            )
            device = Device.objects.create(
                device_id=f"PROJECT-DEVICE-{index}",
                name=f"项目设备 {index}",
                device_type=self.device_type,
            )
            ProjectSensorMember.objects.create(
                project=self.project,
                section=self.section,
                sensor=sensor,
                data_key="value",
                tag=f"S-{index}",
            )
            ProjectDeviceMember.objects.create(
                project=self.project,
                section=self.section,
                device=device,
                tag=f"D-{index}",
            )
            SensorData.objects.create(
                sensor=sensor,
                data={"value": index},
                timestamp=now - timedelta(minutes=5),
            )
            SensorData.objects.create(
                sensor=sensor,
                data={"value": f"future-poison-{index}"},
                timestamp=now + timedelta(days=3650),
            )
            SensorData.objects.create(
                sensor=sensor,
                data={"value": index + 100},
                timestamp=now,
            )
            DeviceStatusCollection.objects.create(
                device=device,
                data={"state": f"old-{index}"},
                event_name="old",
                timestamp=now - timedelta(minutes=5),
            )
            DeviceStatusCollection.objects.create(
                device=device,
                data={"state": f"future-poison-{index}"},
                event_name="future-poison",
                timestamp=now + timedelta(days=3650),
            )
            DeviceStatusCollection.objects.create(
                device=device,
                data={"state": f"latest-{index}"},
                event_name="latest",
                timestamp=now,
            )
            self.sensors.append(sensor)
            self.devices.append(device)

        self.unbound_sensor = Sensor.objects.create(
            sensor_id="PROJECT-SENSOR-UNBOUND",
            name="未绑定传感器",
            sensor_type=self.sensor_type,
        )
        self.unbound_device = Device.objects.create(
            device_id="PROJECT-DEVICE-UNBOUND",
            name="未绑定设备",
            device_type=self.device_type,
        )
        for index in range(3):
            Project.objects.create(
                code=f"QUERY-EMPTY-{index}",
                name=f"空项目 {index}",
            )

    def test_project_list_counts_use_one_annotated_page_query(self):
        with self.assertNumQueries(2):
            response = self.client.get(reverse("project-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = next(
            item for item in response.data["results"]
            if item["id"] == self.project.id
        )
        self.assertEqual(row["section_count"], 1)
        self.assertEqual(row["sensor_count"], 5)
        self.assertEqual(row["device_count"], 5)
        self.assertEqual(row["view_count"], 1)

    @patch("devices.online_status.get_device_offline_timeout", return_value=300)
    def test_snapshot_bulk_loads_latest_member_values(self, _offline_timeout):
        with self.assertNumQueries(3):
            response = self.client.get(
                reverse("project-snapshot", args=[self.project.id])
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["samples"]), 5)
        self.assertEqual(len(response.data["devices"]), 5)
        self.assertEqual(response.data["samples"][0]["value"], 100.0)
        self.assertEqual(
            response.data["devices"][0]["status"],
            {"state": "latest-0"},
        )
        self.assertEqual(response.data["devices"][0]["event"], "latest")

    def test_bindable_sources_bulk_loads_project_membership(self):
        with self.assertNumQueries(5):
            response = self.client.get(
                reverse("project-bindable-sources", args=[self.project.id]),
                {"section": self.section.id},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        bound_sensor = next(
            row for row in response.data["sensors"]
            if row["id"] == self.sensors[0].id
        )
        unbound_sensor = next(
            row for row in response.data["sensors"]
            if row["id"] == self.unbound_sensor.id
        )
        bound_device = next(
            row for row in response.data["devices"]
            if row["id"] == self.devices[0].id
        )
        unbound_device = next(
            row for row in response.data["devices"]
            if row["id"] == self.unbound_device.id
        )
        self.assertEqual(bound_sensor["bound_data_keys"], ["value"])
        self.assertEqual(unbound_sensor["bound_data_keys"], [])
        self.assertTrue(bound_device["already_bound"])
        self.assertFalse(unbound_device["already_bound"])
