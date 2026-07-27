import logging

from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .api_views import (
    backend_health_check,
    dashboard_stats,
    health_check,
    mqtt_status,
)
from .auth_views import register, user_profile, change_password

logger = logging.getLogger(__name__)

urlpatterns = [
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("admin/", admin.site.urls),

    # 用户认证 API
    path("api/auth/login/", TokenObtainPairView.as_view(), name="token-obtain"),
    path("api/auth/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("api/auth/register/", register, name="register"),
    path("api/auth/profile/", user_profile, name="user-profile"),
    path("api/auth/change-password/", change_password, name="change-password"),

    # REST API
    path("api/", include("sensors.urls")),
    path("api/", include("devices.urls")),
    path("api/", include("automation.urls")),
    path("api/", include("projects.urls")),
    path("api/", include("resource_folders.urls")),
    path("api/", include("platform_settings.urls")),
    path("api/mqtt/status/", mqtt_status, name="mqtt-status"),
    path("api/dashboard/stats/", dashboard_stats, name="dashboard-stats"),
    path("health/backend/", backend_health_check, name="backend-health-check"),
    path("health/", health_check, name="health-check"),
]


# 自动挂载已启用的插件路由到 /api/plugins/<name>/
# 单个插件 import/include 失败不应影响其它路由
def _mount_enabled_plugins():
    try:
        from plugins import discover_plugins, enabled_plugin_names
    except Exception as e:
        logger.warning(f"[plugins] 模块加载失败，跳过挂载: {e}")
        return

    enabled = enabled_plugin_names()
    for meta in discover_plugins():
        if meta.name not in enabled:
            continue
        try:
            urlpatterns.append(
                path(f"api/plugins/{meta.name}/", include(meta.url_module))
            )
            logger.info(f"[plugins] 已挂载: /api/plugins/{meta.name}/")
        except Exception as e:
            logger.exception(f"[plugins] 挂载 {meta.name} 失败，已跳过: {e}")


_mount_enabled_plugins()
