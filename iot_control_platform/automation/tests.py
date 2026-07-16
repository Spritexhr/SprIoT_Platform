import copy
import multiprocessing
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from devices.models import Device, DeviceType
from projects.models import Project, ProjectDeviceMember, ProjectSection, ProjectSensorMember
from sensors.models import Sensor, SensorData, SensorType

from .head_files.devices import build_devices
from .admin import AutomationRuleAdmin
from .controllers import run_control_scheme_locked
from .engine import AutomationRuleExecutionError, execute_rule
from .execution_policy import (
    AutomationScriptExecutionDisabled,
    SCRIPT_EXECUTION_DISABLED_CODE,
    SCRIPT_EXECUTION_DISABLED_MESSAGE,
)
from .executor import (
    AutomationExecutionLockUnavailable,
    AutomationExecutionResult,
    AutomationRuleBusy,
    AutomationScriptExecutionTimeout,
    AutomationScriptRemoteError,
    execute_rule_with_timeout_details,
)
from .models import AutomationRule, ControlScheme
from .resources import RuleResourceUnavailable, effective_device_list
from .scheduler import _process_automation_rules, _process_control_schemes


class _InMemoryExecutionLockClient:
    """只模拟父进程 Redis 锁协议；spawn 子进程不继承此测试对象。"""

    def __init__(self, *, set_error=None, eval_error=None):
        self.values = {}
        self.set_error = set_error
        self.eval_error = eval_error
        self.set_calls = []
        self.eval_calls = []

    def set(self, key, value, *, nx, ex):
        self.set_calls.append((key, value, nx, ex))
        if self.set_error is not None:
            raise self.set_error
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, script, numkeys, key, owner):
        self.eval_calls.append((script, numkeys, key, owner))
        if self.eval_error is not None:
            raise self.eval_error
        if self.values.get(key) != owner:
            return 0
        del self.values[key]
        return 1


@override_settings(AUTOMATION_SCRIPT_EXECUTION_ENABLED=True)
class ProjectAutomationRuleApiTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.superuser = user_model.objects.create_superuser(
            username="root", password="test-password", email="root@example.com"
        )
        self.staff = user_model.objects.create_user(
            username="staff", password="test-password", is_staff=True
        )
        self.viewer = user_model.objects.create_user(
            username="viewer", password="test-password"
        )

        self.sensor_type = SensorType.objects.create(
            SensorType_id="temperature-type",
            name="温度传感器",
            data_fields=["temperature"],
        )
        self.sensor = Sensor.objects.create(
            sensor_id="sensor-1",
            name="温度一",
            sensor_type=self.sensor_type,
        )
        self.other_sensor = Sensor.objects.create(
            sensor_id="sensor-2",
            name="温度二",
            sensor_type=self.sensor_type,
        )
        self.device_type = DeviceType.objects.create(
            DeviceType_id="relay-type",
            name="继电器",
            commands={"turn_on": {"params": []}},
        )
        self.device = Device.objects.create(
            device_id="device-1",
            name="继电器一",
            device_type=self.device_type,
        )
        self.other_device = Device.objects.create(
            device_id="device-2",
            name="继电器二",
            device_type=self.device_type,
        )
        self.project = Project.objects.create(code="P1", name="项目一")
        self.section = ProjectSection.objects.create(project=self.project, name="房间一")
        self.other_section = ProjectSection.objects.create(project=self.project, name="房间二")
        self.sensor_member = ProjectSensorMember.objects.create(
            project=self.project,
            section=self.section,
            sensor=self.sensor,
            tag="TT-1",
        )
        self.device_member = ProjectDeviceMember.objects.create(
            project=self.project,
            section=self.section,
            device=self.device,
            tag="K-1",
        )

    def rule_payload(self, **overrides):
        payload = {
            "name": "房间脚本",
            "script_id": "room_script",
            "project": self.project.id,
            "section": self.section.id,
            "script": "from engine import sensors\n\ndef loop():\n    return sensors.get('sensor-1') is not None",
            "device_list": [
                {"device_id": self.sensor.sensor_id, "device_type": "Sensor", "name": "温度"},
                {"device_id": self.device.device_id, "device_type": "Device", "name": "继电器"},
            ],
            "poll_interval": 5,
        }
        payload.update(overrides)
        return payload

    def create_rule(self, **overrides):
        self.client.force_authenticate(self.superuser)
        response = self.client.post(
            reverse("automation-rule-list"), self.rule_payload(**overrides), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return AutomationRule.objects.get(pk=response.data["id"])

    def control_scheme_payload(self, **overrides):
        payload = {
            "name": "房间双位控制",
            "project": self.project.id,
            "section": self.section.id,
            "sensor_member": self.sensor_member.id,
            "data_key": "temperature",
            "device_member": self.device_member.id,
            "control_type": "on_off",
            "setpoint": 25,
            "action": "cool",
            "sample_interval": 5,
            "output_mode": "switch",
            "params": {
                "deadband": 1,
                "switch": {
                    "on_command": "turn_on",
                    "off_command": "turn_off",
                },
            },
        }
        payload.update(overrides)
        return payload

    def create_control_scheme(self, *, enabled=False, **overrides):
        values = {
            "name": "房间双位控制",
            "project": self.project,
            "section": self.section,
            "sensor_member": self.sensor_member,
            "data_key": "temperature",
            "device_member": self.device_member,
            "control_type": "on_off",
            "setpoint": 25,
            "action": "cool",
            "sample_interval": 5,
            "output_mode": "switch",
            "params": {
                "deadband": 1,
                "switch": {
                    "on_command": "turn_on",
                    "off_command": "turn_off",
                },
            },
            "is_enabled": enabled,
            "status": "running" if enabled else "idle",
        }
        values.update(overrides)
        return ControlScheme.objects.create(**values)

    def test_project_rule_only_accepts_imported_room_resources(self):
        self.client.force_authenticate(self.superuser)
        payload = self.rule_payload(
            device_list=[
                {"device_id": self.other_sensor.sensor_id, "device_type": "Sensor"},
            ]
        )
        response = self.client.post(reverse("automation-rule-list"), payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("未导入当前房间", str(response.data))

    @patch("services.devices_service.device_command_send_service.device_command_send_service.send_command_with_make_sure")
    @patch("services.devices_service.device_command_send_service.device_command_send_service.send_command")
    def test_script_device_exposes_normal_and_confirmed_send(
        self, send_command, send_command_with_make_sure
    ):
        send_command.return_value = True
        send_command_with_make_sure.return_value = True
        devices = build_devices([
            {"device_id": self.device.device_id, "device_type": "Device"},
        ])
        wrapper = devices.get(self.device.device_id)

        self.assertTrue(wrapper.send_command("turn_on", {}))
        send_command.assert_called_once_with(
            object_id=self.device.device_id,
            command_name="turn_on",
            params={},
        )

        self.assertTrue(wrapper.send_command_with_make_sure("turn_on", {}, timeout=5))
        send_command_with_make_sure.assert_called_once_with(
            object_id=self.device.device_id,
            command_name="turn_on",
            params={},
            timeout=5,
        )
        for invalid_timeout in (True, 61, float("inf")):
            with self.subTest(timeout=invalid_timeout), self.assertRaises(ValueError):
                wrapper.send_command_with_make_sure(
                    "turn_on",
                    {},
                    timeout=invalid_timeout,
                )

    def test_project_and_section_must_match(self):
        other_project = Project.objects.create(code="P2", name="项目二")
        wrong_section = ProjectSection.objects.create(project=other_project, name="房间")
        self.client.force_authenticate(self.superuser)
        response = self.client.post(
            reverse("automation-rule-list"),
            self.rule_payload(section=wrong_section.id),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("不属于所选项目", str(response.data))

    def test_available_sources_are_limited_to_imported_room_members(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.get(
            reverse("automation-rule-available-sources"),
            {"project": self.project.id, "section": self.section.id},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data["sensors"]], ["sensor-1"])
        self.assertEqual([row["id"] for row in response.data["devices"]], ["device-1"])

    def test_list_can_filter_by_project_and_section_and_exposes_scope(self):
        rule = self.create_rule()
        AutomationRule.objects.create(
            name="全局脚本", script_id="global_script", script="def loop(): return True"
        )
        self.client.force_authenticate(self.viewer)
        response = self.client.get(
            reverse("automation-rule-list"),
            {"project": self.project.id, "section": self.section.id},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data.get("results", response.data)
        self.assertEqual([row["id"] for row in rows], [rule.id])
        self.assertEqual(rows[0]["project_code"], "P1")
        self.assertEqual(rows[0]["section_name"], "房间一")

    def test_scope_cannot_be_changed_after_creation(self):
        rule = self.create_rule()
        self.client.force_authenticate(self.superuser)
        response = self.client.patch(
            reverse("automation-rule-detail", args=[rule.id]),
            {"section": self.other_section.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("不能修改所属房间", str(response.data))

    def test_removed_member_blocks_execution_and_launch(self):
        rule = self.create_rule()
        self.sensor_member.delete()

        with self.assertRaises(RuleResourceUnavailable):
            effective_device_list(rule)

        self.client.force_authenticate(self.superuser)
        execute_response = self.client.post(reverse("automation-rule-execute", args=[rule.id]))
        launch_response = self.client.post(reverse("automation-rule-launch", args=[rule.id]))
        self.assertEqual(execute_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(launch_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scheduler_stops_rule_after_member_is_removed(self):
        rule = self.create_rule()
        rule.is_launched = True
        rule.process_status = "running"
        rule.save(update_fields=["is_launched", "process_status"])
        self.sensor_member.delete()

        with self.assertRaises(RuleResourceUnavailable):
            effective_device_list(rule)
        with patch(
            "automation.scheduler.execute_rule_with_timeout",
            side_effect=RuleResourceUnavailable("以下资源已不属于规则所在房间：Sensor:sensor-1"),
        ):
            _process_automation_rules(timezone.now())

        rule.refresh_from_db()
        self.assertFalse(rule.is_launched)
        self.assertEqual(rule.process_status, "error_stopped")
        self.assertIn("不属于规则所在房间", rule.error_message)

    def test_engine_and_model_do_not_swallow_script_exceptions(self):
        rule = self.create_rule(
            device_list=[],
            script="def loop():\n    raise RuntimeError('boom')",
        )

        with self.assertRaisesRegex(RuntimeError, "boom"):
            execute_rule(rule)

        rule.script = "value = 1"
        with self.assertRaisesRegex(AutomationRuleExecutionError, "未找到.*loop"):
            execute_rule(rule)

    def test_scheduler_marks_false_and_exception_as_error_stopped(self):
        rule = self.create_rule(device_list=[], script="def loop(): return False")
        rule.is_launched = True
        rule.process_status = "running"
        rule.last_run_time = None
        rule.save(update_fields=["is_launched", "process_status", "last_run_time"])

        with patch("automation.scheduler.execute_rule_with_timeout", return_value=False):
            _process_automation_rules(timezone.now())

        rule.refresh_from_db()
        self.assertFalse(rule.is_launched)
        self.assertEqual(rule.process_status, "error_stopped")
        self.assertIn("loop() 返回 False", rule.error_message)

        rule.is_launched = True
        rule.process_status = "running"
        rule.error_message = ""
        rule.last_run_time = None
        rule.save(update_fields=[
            "is_launched", "process_status", "error_message", "last_run_time",
        ])
        with patch(
            "automation.scheduler.execute_rule_with_timeout",
            side_effect=RuntimeError("boom"),
        ):
            _process_automation_rules(timezone.now())

        rule.refresh_from_db()
        self.assertFalse(rule.is_launched)
        self.assertEqual(rule.process_status, "error_stopped")
        self.assertIn("boom", rule.error_message)

    def test_staff_cannot_mutate_or_execute_project_rule(self):
        rule = self.create_rule(device_list=[])
        self.client.force_authenticate(self.staff)
        create_response = self.client.post(
            reverse("automation-rule-list"), self.rule_payload(script_id="staff_script"), format="json"
        )
        update_response = self.client.patch(
            reverse("automation-rule-detail", args=[rule.id]),
            {"name": "管理人员修改"},
            format="json",
        )
        execute_response = self.client.post(reverse("automation-rule-execute", args=[rule.id]))
        launch_response = self.client.post(reverse("automation-rule-launch", args=[rule.id]))
        stop_response = self.client.post(reverse("automation-rule-stop", args=[rule.id]))
        bulk_response = self.client.post(
            reverse("automation-rule-bulk-move"),
            {"rule_ids": [rule.id], "folder": None},
            format="json",
        )
        reorder_response = self.client.post(
            reverse("automation-rule-reorder"),
            {"order": [rule.id], "folder": None},
            format="json",
        )
        delete_response = self.client.delete(
            reverse("automation-rule-detail", args=[rule.id])
        )

        for response in (
            create_response, update_response, execute_response, launch_response,
            stop_response, bulk_response, reorder_response, delete_response,
        ):
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)

        self.client.force_authenticate(self.viewer)
        read_response = self.client.get(reverse("automation-rule-detail", args=[rule.id]))
        self.assertEqual(read_response.status_code, status.HTTP_200_OK)

    def test_superuser_can_mutate_and_control_project_rule(self):
        rule = self.create_rule(device_list=[], script="def loop(): return True")

        update_response = self.client.patch(
            reverse("automation-rule-detail", args=[rule.id]),
            {"name": "超级用户修改"},
            format="json",
        )
        with patch(
            "automation.executor.execute_rule_with_timeout_details",
            return_value=AutomationExecutionResult(
                success=True,
                output="子进程输出\n",
                logs=({"level": "INFO", "message": "[automation] 已执行"},),
            ),
        ):
            execute_response = self.client.post(
                reverse("automation-rule-execute", args=[rule.id])
            )
        launch_response = self.client.post(reverse("automation-rule-launch", args=[rule.id]))
        stop_response = self.client.post(reverse("automation-rule-stop", args=[rule.id]))
        bulk_response = self.client.post(
            reverse("automation-rule-bulk-move"),
            {"rule_ids": [rule.id], "folder": None},
            format="json",
        )
        reorder_response = self.client.post(
            reverse("automation-rule-reorder"),
            {"order": [rule.id], "folder": None},
            format="json",
        )
        delete_response = self.client.delete(reverse("automation-rule-detail", args=[rule.id]))

        self.assertEqual(update_response.status_code, status.HTTP_200_OK, update_response.data)
        self.assertEqual(execute_response.status_code, status.HTTP_200_OK, execute_response.data)
        self.assertEqual(execute_response.data["output"], "子进程输出\n")
        self.assertEqual(execute_response.data["logs"][0]["level"], "INFO")
        self.assertEqual(launch_response.status_code, status.HTTP_200_OK, launch_response.data)
        self.assertEqual(stop_response.status_code, status.HTTP_200_OK, stop_response.data)
        self.assertEqual(bulk_response.status_code, status.HTTP_200_OK, bulk_response.data)
        self.assertEqual(reorder_response.status_code, status.HTTP_200_OK, reorder_response.data)
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)

    def test_execute_api_preserves_remote_output_logs_and_traceback(self):
        rule = self.create_rule(device_list=[], script="def loop(): return True")
        remote_error = AutomationScriptRemoteError(
            error_type="builtins.ValueError",
            message="boom",
            remote_traceback="Traceback (most recent call last):\nValueError: boom",
            output="异常前输出\n",
            logs=({"level": "ERROR", "message": "[automation.engine] boom"},),
        )

        with patch(
            "automation.executor.execute_rule_with_timeout_details",
            side_effect=remote_error,
        ):
            response = self.client.post(
                reverse("automation-rule-execute", args=[rule.id])
            )

        self.assertEqual(
            response.status_code,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            response.data,
        )
        self.assertEqual(response.data["output"], "异常前输出\n")
        self.assertEqual(response.data["logs"][0]["level"], "ERROR")
        self.assertEqual(response.data["error_type"], "builtins.ValueError")
        self.assertIn("ValueError: boom", response.data["traceback"])

    def test_execute_api_reports_timeout_busy_and_lock_outage(self):
        rule = self.create_rule(device_list=[], script="def loop(): return True")
        cases = (
            (
                AutomationScriptExecutionTimeout(rule.id, 1),
                status.HTTP_504_GATEWAY_TIMEOUT,
                "automation_script_timeout",
            ),
            (
                AutomationRuleBusy(rule.id),
                status.HTTP_409_CONFLICT,
                "automation_rule_busy",
            ),
            (
                AutomationExecutionLockUnavailable("redis unavailable"),
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "automation_execution_lock_unavailable",
            ),
        )

        for error, expected_status, expected_code in cases:
            with self.subTest(code=expected_code), patch(
                "automation.executor.execute_rule_with_timeout_details",
                side_effect=error,
            ):
                response = self.client.post(
                    reverse("automation-rule-execute", args=[rule.id])
                )
            self.assertEqual(response.status_code, expected_status, response.data)
            self.assertEqual(response.data["code"], expected_code)

    def test_staff_cannot_create_global_rule(self):
        self.client.force_authenticate(self.staff)
        response = self.client.post(
            reverse("automation-rule-list"),
            {
                "name": "全局脚本",
                "script_id": "staff_global_rule",
                "script": "def loop(): return True",
                "device_list": [],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(AUTOMATION_SCRIPT_EXECUTION_ENABLED=False)
    def test_disabled_switch_blocks_api_model_scheduler_and_admin_execution(self):
        rule = self.create_rule(device_list=[], script="def loop(): return True")
        self.client.force_authenticate(self.superuser)

        with patch.object(AutomationRule, "execute") as model_execute:
            execute_response = self.client.post(
                reverse("automation-rule-execute", args=[rule.id])
            )
        launch_response = self.client.post(reverse("automation-rule-launch", args=[rule.id]))

        self.assertEqual(execute_response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(launch_response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(execute_response.data["code"], SCRIPT_EXECUTION_DISABLED_CODE)
        self.assertIn("与服务容器具有相同操作系统权限", execute_response.data["detail"])
        model_execute.assert_not_called()

        rule.refresh_from_db()
        self.assertFalse(rule.is_launched)
        self.assertEqual(rule.process_status, "idle")
        with self.assertRaises(AutomationScriptExecutionDisabled):
            rule.execute()
        with self.assertRaises(AutomationScriptExecutionDisabled):
            AutomationRule.execute_by_script_id(rule.script_id)
        with self.assertRaises(AutomationScriptExecutionDisabled):
            AutomationRule.execute_by_script_id_with_timed_polling(rule.script_id, 1)
        from .engine import execute_rule as execute_rule_direct
        with self.assertRaises(AutomationScriptExecutionDisabled):
            execute_rule_direct(rule)

        rule.is_launched = True
        rule.process_status = "running"
        rule.error_message = ""
        rule.save(update_fields=["is_launched", "process_status", "error_message"])
        with patch("automation.scheduler.execute_rule_with_timeout") as execute_rule:
            _process_automation_rules(timezone.now())
        execute_rule.assert_not_called()
        rule.refresh_from_db()
        self.assertFalse(rule.is_launched)
        self.assertEqual(rule.process_status, "error_stopped")
        self.assertEqual(rule.error_message, SCRIPT_EXECUTION_DISABLED_MESSAGE)

        request = RequestFactory().post("/admin/automation/automationrule/")
        request.user = self.superuser
        model_admin = AutomationRuleAdmin(AutomationRule, AdminSite())
        with (
            patch.object(AutomationRule, "execute") as admin_execute,
            patch.object(model_admin, "message_user") as message_user,
        ):
            model_admin.test_execute(request, AutomationRule.objects.filter(pk=rule.pk))
        admin_execute.assert_not_called()
        self.assertIn(SCRIPT_EXECUTION_DISABLED_MESSAGE, message_user.call_args.args)

    def test_admin_rule_mutations_are_superuser_only(self):
        model_admin = AutomationRuleAdmin(AutomationRule, AdminSite())
        staff_request = RequestFactory().get("/admin/automation/automationrule/")
        staff_request.user = self.staff
        super_request = RequestFactory().get("/admin/automation/automationrule/")
        super_request.user = self.superuser

        self.assertFalse(model_admin.has_add_permission(staff_request))
        self.assertFalse(model_admin.has_change_permission(staff_request))
        self.assertFalse(model_admin.has_delete_permission(staff_request))
        self.assertTrue(model_admin.has_add_permission(super_request))
        self.assertTrue(model_admin.has_change_permission(super_request))
        self.assertTrue(model_admin.has_delete_permission(super_request))

    def test_control_scheme_is_read_only_for_viewer_and_editable_by_staff(self):
        payload = {
            "name": "房间 PI 控制",
            "project": self.project.id,
            "section": self.section.id,
            "sensor_member": self.sensor_member.id,
            "data_key": "temperature",
            "device_member": self.device_member.id,
            "control_type": "pi",
            "setpoint": 25,
            "action": "cool",
            "sample_interval": 5,
            "output_mode": "analog",
            "params": {"analog": {"command": "turn_on"}},
        }

        self.client.force_authenticate(self.staff)
        create_response = self.client.post(
            reverse("control-scheme-list"), payload, format="json"
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        scheme_id = create_response.data["id"]

        self.client.force_authenticate(self.viewer)
        read_response = self.client.get(reverse("control-scheme-detail", args=[scheme_id]))
        update_response = self.client.patch(
            reverse("control-scheme-detail", args=[scheme_id]),
            {"name": "访客修改"},
            format="json",
        )
        self.assertEqual(read_response.status_code, status.HTTP_200_OK)
        self.assertEqual(update_response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.staff)
        update_response = self.client.patch(
            reverse("control-scheme-detail", args=[scheme_id]),
            {**payload, "name": "管理人员修改"},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK, update_response.data)
        self.assertEqual(ControlScheme.objects.get(pk=scheme_id).name, "管理人员修改")

    def test_control_scheme_create_and_update_reject_cross_room_members(self):
        other_sensor_member = ProjectSensorMember.objects.create(
            project=self.project,
            section=self.other_section,
            sensor=self.other_sensor,
            tag="TT-2",
        )
        other_device_member = ProjectDeviceMember.objects.create(
            project=self.project,
            section=self.other_section,
            device=self.other_device,
            tag="K-2",
        )
        self.client.force_authenticate(self.staff)

        create_response = self.client.post(
            reverse("control-scheme-list"),
            self.control_scheme_payload(
                sensor_member=other_sensor_member.id,
                device_member=other_device_member.id,
            ),
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("同一项目和房间", str(create_response.data["sensor_member"]))
        self.assertIn("同一项目和房间", str(create_response.data["device_member"]))

        valid_response = self.client.post(
            reverse("control-scheme-list"),
            self.control_scheme_payload(),
            format="json",
        )
        self.assertEqual(valid_response.status_code, status.HTTP_201_CREATED, valid_response.data)
        update_response = self.client.patch(
            reverse("control-scheme-detail", args=[valid_response.data["id"]]),
            {"sensor_member": other_sensor_member.id},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("同一项目和房间", str(update_response.data["sensor_member"]))

    @patch(
        "services.devices_service.device_command_send_service."
        "device_command_send_service.send_command_with_make_sure"
    )
    @patch(
        "services.devices_service.device_command_send_service."
        "device_command_send_service.send_command"
    )
    def test_step_string_false_is_dry_run_and_never_sends(
        self, send_command, send_command_with_make_sure
    ):
        scheme = self.create_control_scheme(enabled=True)
        SensorData.objects.create(
            sensor=self.sensor,
            data={"temperature": 30},
            timestamp=timezone.now(),
        )
        self.client.force_authenticate(self.staff)

        response = self.client.post(
            reverse("control-scheme-step", args=[scheme.pk]),
            {"send": "false"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIsNone(response.data["sent"])
        self.assertIsNone(response.data["error"])
        send_command.assert_not_called()
        send_command_with_make_sure.assert_not_called()

        invalid_response = self.client.post(
            reverse("control-scheme-step", args=[scheme.pk]),
            {"send": "yes"},
            format="json",
        )
        self.assertEqual(invalid_response.status_code, status.HTTP_400_BAD_REQUEST)
        send_command.assert_not_called()

    @patch(
        "services.devices_service.device_command_send_service."
        "device_command_send_service.send_command_with_make_sure"
    )
    @patch(
        "services.devices_service.device_command_send_service."
        "device_command_send_service.send_command",
        return_value=False,
    )
    def test_step_publish_failure_is_error_and_does_not_wait_for_ack(
        self, send_command, send_command_with_make_sure
    ):
        scheme = self.create_control_scheme(enabled=True)
        SensorData.objects.create(
            sensor=self.sensor,
            data={"temperature": 30},
            timestamp=timezone.now(),
        )
        self.client.force_authenticate(self.staff)

        response = self.client.post(
            reverse("control-scheme-step", args=[scheme.pk]),
            {"send": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY, response.data)
        self.assertIs(response.data["sent"], False)
        self.assertEqual(response.data["error_code"], "command_delivery_failed")
        send_command.assert_called_once_with(
            object_id=self.device.device_id,
            command_name="turn_on",
            params={},
        )
        send_command_with_make_sure.assert_not_called()
        scheme.refresh_from_db()
        self.assertFalse(scheme.is_enabled)
        self.assertEqual(scheme.status, "error")
        self.assertIn("下发失败", scheme.error_message)

    def test_locked_step_rechecks_due_time_after_acquiring_lock(self):
        now = timezone.now()
        scheme = self.create_control_scheme(
            enabled=True,
            last_run_time=now,
        )

        manager = ControlScheme.objects
        with (
            patch.object(
                manager,
                "select_for_update",
                wraps=manager.select_for_update,
            ) as select_for_update,
            patch("automation.controllers.run_control_scheme") as run_step,
        ):
            result = run_control_scheme_locked(scheme.pk, send=True, due_at=now)

        self.assertTrue(result["skipped"])
        self.assertIsNone(result["sent"])
        select_for_update.assert_called_once_with()
        run_step.assert_not_called()

    def test_scheduler_uses_locked_step_and_stops_on_sent_false(self):
        scheme = self.create_control_scheme(enabled=True)
        now = timezone.now()
        failed_result = {
            "pv": 30,
            "output": 100,
            "command": "turn_on",
            "params": {},
            "sent": False,
            "error": None,
            "error_code": None,
            "skipped": False,
        }

        with patch(
            "automation.scheduler.run_control_scheme_locked",
            return_value=failed_result,
        ) as locked_step:
            _process_control_schemes(now)

        locked_step.assert_called_once_with(scheme.pk, send=True, due_at=now)
        scheme.refresh_from_db()
        self.assertFalse(scheme.is_enabled)
        self.assertEqual(scheme.status, "error")
        self.assertIn("sent=False", scheme.error_message)

    def test_global_rule_keeps_global_resource_behavior(self):
        self.client.force_authenticate(self.superuser)
        response = self.client.post(
            reverse("automation-rule-list"),
            {
                "name": "全局脚本",
                "script_id": "global_rule",
                "script": "def loop(): return True",
                "device_list": [
                    {"device_id": self.other_sensor.sensor_id, "device_type": "Sensor"},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNone(AutomationRule.objects.get(pk=response.data["id"]).section_id)

    def test_deleting_section_deletes_scoped_rule(self):
        rule = self.create_rule()
        self.section.delete()
        self.assertFalse(AutomationRule.objects.filter(pk=rule.id).exists())


class IsolatedAutomationExecutorTests(unittest.TestCase):
    """使用真实文件 SQLite + spawn，避免依赖 fork 继承测试库或 mock。"""

    def setUp(self):
        from django.conf import settings

        self._settings = settings
        self._temp_dir = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self._temp_dir.name, "automation.sqlite3")
        self.database_alias = f"automation_executor_{os.getpid()}_{time.time_ns()}"

        database_settings = copy.deepcopy(connections["default"].settings_dict)
        database_settings.update({
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": self.database_path,
            "USER": "",
            "PASSWORD": "",
            "HOST": "",
            "PORT": "",
            "OPTIONS": {},
            "CONN_MAX_AGE": 0,
            "CONN_HEALTH_CHECKS": False,
        })
        database_settings["TEST"] = copy.deepcopy(database_settings.get("TEST") or {})
        database_settings["TEST"]["NAME"] = None

        connections.settings[self.database_alias] = database_settings
        settings.DATABASES[self.database_alias] = database_settings
        connection = connections[self.database_alias]
        # 只建执行器会访问的规则表，不创建整个项目图；省略 FK 约束，避免隔离库
        # 为三个 nullable 关联字段额外复制无关模型表。
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE automation_automationrule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(100) NOT NULL,
                    description TEXT NOT NULL,
                    project_id BIGINT NULL,
                    section_id BIGINT NULL,
                    script_id VARCHAR(50) NOT NULL UNIQUE,
                    folder_id BIGINT NULL,
                    sort_order INTEGER NOT NULL,
                    script TEXT NOT NULL,
                    device_list TEXT NOT NULL,
                    is_launched BOOLEAN NOT NULL,
                    poll_interval INTEGER NOT NULL,
                    process_status VARCHAR(20) NOT NULL,
                    error_message TEXT NOT NULL,
                    last_run_time DATETIME NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
            """)

        # AutomationRule post_save 会安排实时广播；这里仅构造隔离数据库，不访问 Redis。
        with patch("services.realtime.dispatch.publish_automation_rule"):
            self.rule = AutomationRule.objects.using(self.database_alias).create(
                name="spawn executor test",
                script_id=f"spawn_{time.time_ns()}",
                script="def loop():\n    return True",
                device_list=[],
            )
        connection.close()

    def tearDown(self):
        try:
            if hasattr(connections._connections, self.database_alias):
                connections[self.database_alias].close()
                del connections[self.database_alias]
        finally:
            connections.settings.pop(self.database_alias, None)
            self._settings.DATABASES.pop(self.database_alias, None)
            self._temp_dir.cleanup()

    def _set_script(self, script: str) -> None:
        AutomationRule.objects.using(self.database_alias).filter(pk=self.rule.pk).update(
            script=script
        )
        connections[self.database_alias].close()

    def _execute(self, *, timeout=10.0, allowed_imports=None):
        lock_client = _InMemoryExecutionLockClient()
        overridden = {
            "AUTOMATION_SCRIPT_EXECUTION_ENABLED": True,
            "AUTOMATION_SCRIPT_TIMEOUT_SECONDS": timeout,
        }
        if allowed_imports is not None:
            overridden["AUTOMATION_ALLOWED_IMPORTS"] = allowed_imports
        with (
            override_settings(**overridden),
            patch(
                "automation.executor._get_execution_lock_client",
                return_value=lock_client,
            ),
        ):
            result = execute_rule_with_timeout_details(
                self.rule.pk,
                using=self.database_alias,
            )
        return result, lock_client

    def test_spawn_success_preserves_stdout_and_releases_owner_lock(self):
        self._set_script(
            "def loop():\n"
            "    print('来自 spawn 子进程：成功')\n"
            "    return True"
        )

        result, lock_client = self._execute()

        self.assertTrue(result.success)
        self.assertEqual(result.output, "来自 spawn 子进程：成功\n")
        self.assertEqual(len(lock_client.set_calls), 1)
        self.assertEqual(len(lock_client.eval_calls), 1)
        _, _, key, released_owner = lock_client.eval_calls[0]
        self.assertNotIn(key, lock_client.values)
        self.assertEqual(released_owner, lock_client.set_calls[0][1])

    def test_spawn_exception_preserves_stdout_logs_and_remote_traceback(self):
        self._set_script(
            "def loop():\n"
            "    print('异常前输出')\n"
            "    raise ValueError('boom from child')"
        )

        with self.assertRaises(AutomationScriptRemoteError) as raised:
            self._execute()

        error = raised.exception
        self.assertEqual(error.error_type, "builtins.ValueError")
        self.assertEqual(error.remote_message, "boom from child")
        self.assertEqual(error.output, "异常前输出\n")
        self.assertIn("ValueError: boom from child", error.remote_traceback)
        self.assertTrue(
            any("boom from child" in item["message"] for item in error.logs),
            error.logs,
        )

    def test_spawn_protocol_sanitizes_surrogates_and_broken_exception_str(self):
        self._set_script(
            "class BrokenMessageError(Exception):\n"
            "    def __str__(self):\n"
            "        raise RuntimeError('broken __str__')\n"
            "def loop():\n"
            "    print('\\ud800')\n"
            "    raise BrokenMessageError()"
        )

        with self.assertRaises(AutomationScriptRemoteError) as raised:
            self._execute()

        error = raised.exception
        self.assertIn("\\ud800", error.output)
        self.assertIn("__str__ failed", error.remote_message)
        # 这些文本会进入 DRF JSON；必须保证标准 UTF-8 编码不会再次失败。
        error.output.encode("utf-8")
        error.remote_message.encode("utf-8")
        error.remote_traceback.encode("utf-8")
        str(error).encode("utf-8")

    def test_real_infinite_loop_is_terminated_on_time_without_residual_child(self):
        sentinel_path = os.path.join(self._temp_dir.name, "loop-started")
        self._set_script(
            "import logging, os, signal\n"
            "def loop():\n"
            "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "    print('即将进入无限循环')\n"
            "    logging.getLogger('automation.timeout_test').warning('无限循环已启动')\n"
            f"    fd = os.open({sentinel_path!r}, os.O_CREAT | os.O_WRONLY, 0o600)\n"
            "    os.close(fd)\n"
            "    while True:\n"
            "        pass"
        )
        before_pids = {child.pid for child in multiprocessing.active_children()}
        started_at = time.monotonic()

        with self.assertRaises(AutomationScriptExecutionTimeout) as raised:
            self._execute(
                timeout=3.0,
                allowed_imports=["logging", "os", "signal"],
            )

        elapsed = time.monotonic() - started_at
        after_pids = {child.pid for child in multiprocessing.active_children()}
        self.assertTrue(os.path.exists(sentinel_path), "子进程未进入真实无限循环")
        self.assertIn("即将进入无限循环", raised.exception.output)
        self.assertTrue(
            any("无限循环已启动" in item["message"] for item in raised.exception.logs),
            raised.exception.logs,
        )
        self.assertGreaterEqual(elapsed, 2.5)
        self.assertLess(elapsed, 6.0)
        self.assertFalse(after_pids - before_pids, after_pids - before_pids)

    def test_invalid_timeout_fails_before_redis_or_spawn(self):
        for invalid in (0, -1, 61, float("inf"), "10", True):
            with self.subTest(timeout=invalid), override_settings(
                AUTOMATION_SCRIPT_EXECUTION_ENABLED=True,
                AUTOMATION_SCRIPT_TIMEOUT_SECONDS=invalid,
            ), patch("automation.executor._get_execution_lock_client") as get_lock:
                with self.assertRaises(ImproperlyConfigured):
                    execute_rule_with_timeout_details(
                        self.rule.pk,
                        using=self.database_alias,
                    )
                get_lock.assert_not_called()

    def test_singleflight_busy_and_redis_outage_fail_closed_without_spawn(self):
        lock_key = (
            "spr:iot:automation:execution:"
            f"{self.database_alias}:{self.rule.pk}"
        )
        busy_client = _InMemoryExecutionLockClient()
        busy_client.values[lock_key] = "other-owner"

        with override_settings(
            AUTOMATION_SCRIPT_EXECUTION_ENABLED=True,
            AUTOMATION_SCRIPT_TIMEOUT_SECONDS=10,
            AUTOMATION_SCRIPT_LOCK_PREFIX="spr:iot:automation:execution",
        ), patch(
            "automation.executor._get_execution_lock_client",
            return_value=busy_client,
        ), patch("automation.executor._execute_rule_process_under_lease") as spawn:
            with self.assertRaises(AutomationRuleBusy):
                execute_rule_with_timeout_details(
                    self.rule.pk,
                    using=self.database_alias,
                )
            spawn.assert_not_called()

        unavailable_client = _InMemoryExecutionLockClient(
            set_error=RuntimeError("redis unavailable")
        )
        with override_settings(
            AUTOMATION_SCRIPT_EXECUTION_ENABLED=True,
            AUTOMATION_SCRIPT_TIMEOUT_SECONDS=10,
            AUTOMATION_SCRIPT_LOCK_PREFIX="spr:iot:automation:execution",
        ), patch(
            "automation.executor._get_execution_lock_client",
            return_value=unavailable_client,
        ), patch("automation.executor._execute_rule_process_under_lease") as spawn:
            with self.assertRaises(AutomationExecutionLockUnavailable):
                execute_rule_with_timeout_details(
                    self.rule.pk,
                    using=self.database_alias,
                )
            spawn.assert_not_called()

        replaced_client = _InMemoryExecutionLockClient()

        def replace_owner(*args, **kwargs):
            key = replaced_client.set_calls[0][0]
            replaced_client.values[key] = "new-owner"
            return AutomationExecutionResult(success=True)

        with override_settings(
            AUTOMATION_SCRIPT_EXECUTION_ENABLED=True,
            AUTOMATION_SCRIPT_TIMEOUT_SECONDS=10,
            AUTOMATION_SCRIPT_LOCK_PREFIX="spr:iot:automation:execution",
        ), patch(
            "automation.executor._get_execution_lock_client",
            return_value=replaced_client,
        ), patch(
            "automation.executor._execute_rule_process_under_lease",
            side_effect=replace_owner,
        ):
            with self.assertRaises(AutomationExecutionLockUnavailable):
                execute_rule_with_timeout_details(
                    self.rule.pk,
                    using=self.database_alias,
                )
        self.assertEqual(replaced_client.values[lock_key], "new-owner")
