import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.db import connections, router
from django.db.utils import OperationalError
from django.test import SimpleTestCase, TestCase

from platform_settings.models import Plugin

from . import PluginMeta, discover_plugins, enabled_plugin_names


class PluginManifestDiscoveryTests(SimpleTestCase):
    @staticmethod
    def _write_manifest(root: Path, directory_name: str, manifest) -> None:
        plugin_dir = root / directory_name
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_only_matching_safe_manifest_names_are_discovered(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_manifest(
                root,
                "valid_plugin",
                {"name": "valid_plugin", "enabled": True, "version": "1.0.0"},
            )
            self._write_manifest(
                root,
                "mismatch",
                {"name": "different_name", "enabled": True},
            )
            self._write_manifest(
                root,
                "unsafe-name",
                {"name": "unsafe-name", "enabled": True},
            )
            self._write_manifest(root, "missing_name", {"enabled": True})
            self._write_manifest(
                root,
                "invalid_enabled",
                {"name": "invalid_enabled", "enabled": "false"},
            )
            self._write_manifest(root, "not_an_object", ["not", "an", "object"])

            with patch("plugins.PLUGINS_DIR", root):
                discovered = discover_plugins()

        self.assertEqual([plugin.name for plugin in discovered], ["valid_plugin"])
        self.assertEqual(discovered[0].app_label, "plugins.valid_plugin")


class PluginEnablementTests(TestCase):
    def setUp(self):
        Plugin.objects.all().delete()
        self.meta = PluginMeta(
            name="safe_plugin",
            version="1.0.0",
            description="",
            enabled=True,
            path=Path("/tmp/safe_plugin"),
        )
        alias = router.db_for_read(Plugin)
        self.connection = connections[alias]
        self.table_name = Plugin._meta.db_table

    def test_missing_table_uses_manifest_default(self):
        with (
            patch("plugins.discover_plugins", return_value=[self.meta]),
            patch.object(self.connection.introspection, "table_names", return_value=[]),
        ):
            enabled = enabled_plugin_names()

        self.assertEqual(enabled, {"safe_plugin"})

    def test_database_disabled_state_overrides_enabled_manifest(self):
        Plugin.objects.create(name="safe_plugin", enabled=False)

        with patch("plugins.discover_plugins", return_value=[self.meta]):
            enabled = enabled_plugin_names()

        self.assertEqual(enabled, set())

    def test_runtime_query_error_fails_closed(self):
        manager = Plugin.objects
        with (
            patch("plugins.discover_plugins", return_value=[self.meta]),
            patch.object(
                self.connection.introspection,
                "table_names",
                return_value=[self.table_name],
            ),
            patch.object(
                manager,
                "using",
                side_effect=OperationalError("database connection lost"),
            ),
        ):
            enabled = enabled_plugin_names()

        self.assertEqual(enabled, set())

    def test_table_introspection_error_fails_closed(self):
        with (
            patch("plugins.discover_plugins", return_value=[self.meta]),
            patch.object(
                self.connection.introspection,
                "table_names",
                side_effect=OperationalError("database unavailable"),
            ),
        ):
            enabled = enabled_plugin_names()

        self.assertEqual(enabled, set())
