"""
插件系统 - 自动发现 plugins/ 目录下的 Django app 形式插件

约定：
- 每个子目录是一个独立的 Django app
- 子目录根下需要一份 plugin.json 清单文件
- 子目录内需要标准的 Django app 结构（apps.py / urls.py 等）

启用规则：
- discover_plugins() 仅做文件系统扫描，不依赖数据库
- enabled_plugin_names() 优先读 platform_settings.Plugin 表；
  仅确认表尚未创建（首次 migrate 前）时回退到清单默认值，其余 DB 故障全部禁用
"""
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PLUGINS_DIR = Path(__file__).resolve().parent
MANIFEST_FILENAME = "plugin.json"
PLUGIN_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


@dataclass
class PluginMeta:
    """插件清单 - 来自 plugin.json 的描述信息"""
    name: str           # 插件唯一标识，与目录名一致
    version: str        # 语义化版本，如 "0.1.0"
    description: str    # 简短描述
    enabled: bool       # 默认是否启用（首次 sync 时写入 DB）
    path: Path          # 插件目录绝对路径
    ws_module: Optional[str] = None   # 可选：WebSocket routing 模块路径
                                       # 模块需 export `websocket_urlpatterns`
                                       # 由 config/asgi.py 在启动时动态 import

    @property
    def app_label(self) -> str:
        """Django app 完整路径，用于 INSTALLED_APPS"""
        return f"plugins.{self.name}"

    @property
    def url_module(self) -> str:
        """插件的 urls 模块路径"""
        return f"plugins.{self.name}.urls"


def discover_plugins() -> list[PluginMeta]:
    """
    扫描 plugins/ 子目录，返回所有合法插件的清单
    合法条件：目录名是小写 ASCII 安全标识、包含 plugin.json，且清单 name
    明确存在并与目录名完全一致。
    """
    plugins: list[PluginMeta] = []
    if not PLUGINS_DIR.exists():
        return plugins

    for entry in sorted(PLUGINS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(("_", ".")):
            continue
        manifest_path = entry / MANIFEST_FILENAME
        if not manifest_path.exists():
            continue

        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"插件 {entry.name} 的 plugin.json 解析失败: {e}")
            continue
        if not isinstance(data, dict):
            logger.warning("插件 %s 的 plugin.json 顶层必须是对象", entry.name)
            continue

        manifest_name = data.get("name")
        if (
            not isinstance(manifest_name, str)
            or manifest_name != entry.name
            or PLUGIN_NAME_RE.fullmatch(manifest_name) is None
        ):
            logger.warning(
                "忽略插件目录 %s：plugin.json name 必须与目录名一致，"
                "且匹配 %s（当前为 %r）",
                entry.name,
                PLUGIN_NAME_RE.pattern,
                manifest_name,
            )
            continue

        enabled_raw = data.get("enabled", True)
        if not isinstance(enabled_raw, bool):
            logger.warning(
                "忽略插件 %s：plugin.json enabled 必须是布尔值（当前为 %r）",
                entry.name,
                enabled_raw,
            )
            continue

        ws_module_raw = data.get("ws_module")
        ws_module = str(ws_module_raw) if isinstance(ws_module_raw, str) and ws_module_raw else None
        plugins.append(
            PluginMeta(
                name=manifest_name,
                version=str(data.get("version") or "0.0.0"),
                description=str(data.get("description") or ""),
                enabled=enabled_raw,
                path=entry,
                ws_module=ws_module,
            )
        )
    return plugins


def enabled_plugin_names() -> set[str]:
    """
    返回当前启用的插件名集合
    优先读 DB。仅在确认插件表尚未创建时使用清单默认值；连接失败、权限错误、
    查询异常等运行期故障一律 fail closed（返回空集合），避免把 DB 中已禁用的
    插件按 manifest 默认值误启。
    """
    discovered = discover_plugins()
    discovered_by_name = {p.name: p for p in discovered}

    manifest_defaults = {p.name for p in discovered if p.enabled}

    try:
        # 延迟导入：settings 加载阶段不要触发 ORM。
        from django.db import connections, router  # noqa: WPS433
        from platform_settings.models import Plugin  # noqa: WPS433

        database_alias = router.db_for_read(Plugin)
        connection = connections[database_alias]
        table_name = Plugin._meta.db_table
    except Exception:
        logger.exception("读取插件启停状态前初始化 ORM 失败；所有插件保持禁用")
        return set()

    try:
        table_names = connection.introspection.table_names()
    except Exception:
        logger.exception("检查插件登记表失败；所有插件保持禁用")
        return set()

    if table_name not in table_names:
        logger.info("插件登记表 %s 尚未创建，临时采用 manifest 默认值", table_name)
        return manifest_defaults

    try:
        db_states = dict(
            Plugin.objects.using(database_alias).values_list("name", "enabled")
        )
    except Exception:
        logger.exception("查询插件启停状态失败；所有插件保持禁用")
        return set()

    enabled: set[str] = set()
    for name, meta in discovered_by_name.items():
        # DB 中有记录则以 DB 为准，否则用清单默认
        enabled_flag = db_states.get(name, meta.enabled)
        if enabled_flag:
            enabled.add(name)
    return enabled
