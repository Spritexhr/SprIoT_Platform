"""容器 backend readiness 健康检查测试。"""

from unittest.mock import MagicMock, Mock, patch

from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


class DatabaseReadinessProbeTests(SimpleTestCase):
    def test_mysql_probe_uses_and_closes_an_independent_short_timeout_connection(self):
        from config.api_views import _check_database_connection

        wrapper = Mock()
        wrapper.vendor = "mysql"
        wrapper.get_connection_params.return_value = {
            "host": "mysql",
            "user": "iot",
            "passwd": "secret",
            "db": "iot_platform",
        }
        probe = Mock()
        cursor = Mock()
        cursor.fetchone.return_value = (1,)
        probe.cursor.return_value = cursor
        wrapper.Database.connect.return_value = probe

        with patch("config.api_views.connection", wrapper):
            healthy = _check_database_connection()

        self.assertTrue(healthy)
        wrapper.Database.connect.assert_called_once_with(
            host="mysql",
            user="iot",
            passwd="secret",
            db="iot_platform",
            connect_timeout=1,
            read_timeout=1,
            write_timeout=1,
        )
        cursor.execute.assert_called_once_with("SELECT 1")
        cursor.close.assert_called_once_with()
        probe.close.assert_called_once_with()


class BackendReadinessTests(APITestCase):
    @staticmethod
    def _healthy_cursor():
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        context = MagicMock()
        context.__enter__.return_value = cursor
        return context

    def test_readiness_checks_database_and_redis_without_requiring_runner_online(self):
        with patch(
            "config.api_views.connection.cursor",
            return_value=self._healthy_cursor(),
        ), patch(
            "config.api_views._check_redis_connection",
            return_value=True,
        ) as redis_check:
            response = self.client.get(reverse("backend-health-check"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["checks"],
            {"database": "ok", "redis": "ok"},
        )
        redis_check.assert_called_once_with()

    def test_container_readiness_is_not_blocked_by_anonymous_api_throttle(self):
        with patch(
            "config.api_views.connection.cursor",
            return_value=self._healthy_cursor(),
        ), patch(
            "config.api_views._check_redis_connection",
            return_value=True,
        ):
            statuses = [
                self.client.get(reverse("backend-health-check")).status_code
                for _ in range(105)
            ]

        self.assertEqual(set(statuses), {status.HTTP_200_OK})

    def test_readiness_is_degraded_when_redis_is_unavailable(self):
        with patch(
            "config.api_views.connection.cursor",
            return_value=self._healthy_cursor(),
        ), patch(
            "config.api_views._check_redis_connection",
            side_effect=ConnectionError("redis unavailable"),
        ):
            response = self.client.get(reverse("backend-health-check"))

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertEqual(
            response.data["checks"],
            {"database": "ok", "redis": "error"},
        )

    def test_public_monitoring_health_is_not_exhausted_by_its_own_polling(self):
        bus = Mock()
        bus.get_runner_status.return_value = {
            "is_connected": "1",
            "command_worker_alive": "1",
        }
        with patch(
            "config.api_views.connection.cursor",
            return_value=self._healthy_cursor(),
        ), patch(
            "services.mqtt_command_bus.get_mqtt_command_bus",
            return_value=bus,
        ):
            statuses = [
                self.client.get(reverse("health-check")).status_code
                for _ in range(105)
            ]

        self.assertEqual(set(statuses), {status.HTTP_200_OK})
