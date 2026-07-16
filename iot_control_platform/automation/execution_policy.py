"""自动化 Python 脚本的统一执行策略。

脚本在独立子进程中运行，但仍继承服务容器的操作系统权限。
引擎中的 import / builtins 限制只能减少误操作面，不构成安全沙箱。
因此执行能力默认关闭，且只能由部署配置显式开启。
"""
import math

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


SCRIPT_EXECUTION_DISABLED_CODE = "automation_script_execution_disabled"
SCRIPT_EXECUTION_DISABLED_MESSAGE = (
    "自动化 Python 脚本执行已关闭。仅在确认脚本来源可信，并接受脚本子进程与服务"
    "容器具有相同操作系统权限的风险后，才可显式启用 AUTOMATION_SCRIPT_EXECUTION_ENABLED。"
)
SCRIPT_EXECUTION_TIMEOUT_MAX_SECONDS = 60.0


class AutomationScriptExecutionDisabled(RuntimeError):
    """部署策略禁止执行自动化 Python 脚本。"""

    code = SCRIPT_EXECUTION_DISABLED_CODE


def is_script_execution_enabled() -> bool:
    """仅接受明确的布尔值 True；缺失、字符串或其它真值均按关闭处理。"""
    return getattr(settings, "AUTOMATION_SCRIPT_EXECUTION_ENABLED", False) is True


def ensure_script_execution_enabled() -> None:
    """执行入口的 fail-closed 守卫。"""
    if not is_script_execution_enabled():
        raise AutomationScriptExecutionDisabled(SCRIPT_EXECUTION_DISABLED_MESSAGE)


def get_script_execution_timeout() -> float:
    """读取并二次校验硬超时；运行期 override 也不能绕过正数和上限约束。"""
    value = getattr(settings, "AUTOMATION_SCRIPT_TIMEOUT_SECONDS", 10.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ImproperlyConfigured(
            "AUTOMATION_SCRIPT_TIMEOUT_SECONDS 必须是数值"
        )
    timeout = float(value)
    if (
        not math.isfinite(timeout)
        or timeout <= 0
        or timeout > SCRIPT_EXECUTION_TIMEOUT_MAX_SECONDS
    ):
        raise ImproperlyConfigured(
            "AUTOMATION_SCRIPT_TIMEOUT_SECONDS 必须大于 0 且不超过 "
            f"{SCRIPT_EXECUTION_TIMEOUT_MAX_SECONDS:g} 秒"
        )
    return timeout
