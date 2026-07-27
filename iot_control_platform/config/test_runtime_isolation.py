"""test/local/prod Redis namespace 隔离回归测试。"""

import json
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class RuntimeRedisIsolationTests(SimpleTestCase):
    def test_django_test_process_uses_in_memory_channels_and_unique_bus_prefix(self):
        self.assertEqual(
            settings.CHANNEL_LAYERS["default"]["BACKEND"],
            "channels.layers.InMemoryChannelLayer",
        )
        self.assertIn(":test:", settings.MQTT_BUS_PREFIX)
        self.assertIn(":test:", settings.AUTOMATION_SCRIPT_LOCK_PREFIX)

    def _load_mode(self, *, runtime_env: str, mysql: bool):
        project_dir = Path(__file__).resolve().parent.parent
        environment = os.environ.copy()
        for key in (
            "AUTOMATION_SCRIPT_LOCK_PREFIX",
            "CHANNEL_LAYER_PREFIX",
            "DB_USE_MYSQL",
            "DJANGO_TESTING",
            "IOT_RUNTIME_ENV",
            "MQTT_BUS_PREFIX",
        ):
            environment.pop(key, None)
        environment.update(
            {
                "DEBUG": "True",
                "SECRET_KEY": "test-only-runtime-isolation-key",
                "DB_USE_MYSQL": "true" if mysql else "false",
                "IOT_RUNTIME_ENV": runtime_env,
                "PYTHONPATH": str(project_dir),
            }
        )
        script = """
import json
from config import settings
layer = settings.CHANNEL_LAYERS["default"]
print(json.dumps({
    "backend": layer["BACKEND"],
    "channel_prefix": layer["CONFIG"]["prefix"],
    "mqtt_prefix": settings.MQTT_BUS_PREFIX,
    "automation_prefix": settings.AUTOMATION_SCRIPT_LOCK_PREFIX,
    "runtime_env": settings.IOT_RUNTIME_ENV,
}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=project_dir,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_inherited_test_marker_survives_child_argv_rewrite(self):
        project_dir = Path(__file__).resolve().parent.parent
        environment = os.environ.copy()
        environment.update(
            {
                "DEBUG": "True",
                "SECRET_KEY": "test-only-runtime-isolation-key",
                "DJANGO_TESTING": "1",
                "MQTT_BUS_PREFIX": "spr:iot:mqtt",
                "AUTOMATION_SCRIPT_LOCK_PREFIX": "spr:iot:automation:execution",
                "PYTHONPATH": str(project_dir),
            }
        )
        script = """
import json
import sys
sys.argv = [sys.argv[0], "automation_executor_child"]
from config import settings
print(json.dumps({
    "backend": settings.CHANNEL_LAYERS["default"]["BACKEND"],
    "mqtt_prefix": settings.MQTT_BUS_PREFIX,
    "automation_prefix": settings.AUTOMATION_SCRIPT_LOCK_PREFIX,
}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=project_dir,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        loaded = json.loads(result.stdout.strip().splitlines()[-1])

        self.assertEqual(
            loaded["backend"],
            "channels.layers.InMemoryChannelLayer",
        )
        self.assertIn(":test:", loaded["mqtt_prefix"])
        self.assertIn(":test:", loaded["automation_prefix"])

    def test_local_mode_cannot_share_production_channels_or_command_bus(self):
        loaded = self._load_mode(runtime_env="local", mysql=False)

        self.assertEqual(loaded["runtime_env"], "local")
        self.assertEqual(loaded["channel_prefix"], "spr_iot_local")
        self.assertEqual(loaded["mqtt_prefix"], "spr:iot:mqtt:local")
        self.assertEqual(
            loaded["automation_prefix"],
            "spr:iot:automation:local:execution",
        )

    def test_production_mode_keeps_production_namespaces(self):
        loaded = self._load_mode(runtime_env="production", mysql=True)

        self.assertEqual(loaded["runtime_env"], "production")
        self.assertEqual(loaded["channel_prefix"], "asgi")
        self.assertEqual(loaded["mqtt_prefix"], "spr:iot:mqtt")
        self.assertEqual(
            loaded["automation_prefix"],
            "spr:iot:automation:execution",
        )

    def test_local_mysql_does_not_switch_to_production_namespaces(self):
        loaded = self._load_mode(runtime_env="local", mysql=True)

        self.assertEqual(loaded["channel_prefix"], "spr_iot_local")
        self.assertEqual(loaded["mqtt_prefix"], "spr:iot:mqtt:local")
        self.assertEqual(
            loaded["automation_prefix"],
            "spr:iot:automation:local:execution",
        )
