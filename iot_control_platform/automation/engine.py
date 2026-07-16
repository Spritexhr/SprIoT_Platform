"""
自动化规则子进程内引擎
脚本通过 from engine import sensors, devices 获取依赖。

支持两种写法：
  1. 类风格：定义含 loop() 方法的类（引擎自动实例化并调用）
  2. 函数风格：直接定义顶层 loop() 函数

风险边界：
- 脚本与 Django 进程同权限运行，不是安全沙箱，只能执行受信任代码
- import 白名单和内置函数限制仅用于减少误操作面，不能作为隔离边界
- 执行能力还受默认关闭的 AUTOMATION_SCRIPT_EXECUTION_ENABLED 显式开关控制
"""
import logging
import types
from typing import Optional

import builtins as _bi

from .execution_policy import ensure_script_execution_enabled

logger = logging.getLogger(__name__)


class AutomationRuleExecutionError(RuntimeError):
    """规则脚本结构不完整，无法形成一次可执行的自动化拍。"""


# 允许脚本 import 的模块白名单（前缀匹配）
_BASE_IMPORT_WHITELIST = ('engine', 'automation.head_files')

def _get_import_whitelist():
    """动态获取导入白名单，结合基础白名单和 settings 配置"""
    from django.conf import settings
    allowed = list(_BASE_IMPORT_WHITELIST)
    extra = getattr(settings, 'AUTOMATION_ALLOWED_IMPORTS', [])
    if extra:
        allowed.extend(extra)
    return tuple(allowed)

# 从脚本的直接 builtins 映射移除这些入口；仅用于减少误操作，不代表能力隔离。
_BLOCKED_BUILTINS = frozenset({
    'open', 'eval', 'exec', 'compile',
    '__import__', 'globals', 'locals',
    'breakpoint', 'input',
})


def _make_restricted_builtins(custom_import):
    """构建受限内置函数字典；这是误操作防护，不是安全沙箱。"""
    restricted = dict(vars(_bi))
    for name in _BLOCKED_BUILTINS:
        restricted.pop(name, None)
    restricted['__import__'] = custom_import
    return restricted


def _make_custom_import(engine_module, real_import):
    """创建自定义 import：仅白名单模块可以通过，其余一律拒绝"""
    def _custom_import(name, *args, **kwargs):
        if name == 'engine':
            return engine_module
        whitelist = _get_import_whitelist()
        if any(name == prefix or name.startswith(prefix + '.') for prefix in whitelist):
            return real_import(name, *args, **kwargs)
        raise ImportError(
            f"自动化脚本不允许导入模块 '{name}'，"
            f"仅允许: {', '.join(whitelist)}"
        )
    return _custom_import


def execute_rule(rule) -> bool:
    """
    在已经初始化好的执行子进程内执行单条自动化规则。
    注入 engine 模块（含 sensors、devices），先尝试找带 loop() 方法的类，
    未找到则尝试顶层 loop() 函数。
    import / builtins 限制不构成安全沙箱；脚本必须来自受信任的超级用户。
    """
    ensure_script_execution_enabled()
    if not rule.script:
        raise AutomationRuleExecutionError("规则脚本为空")

    from automation.head_files.sensors import build_sensors
    from automation.head_files.devices import build_devices
    from automation.resources import effective_device_list

    device_list = effective_device_list(rule)
    sensors = build_sensors(device_list)
    devices = build_devices(device_list)

    # 构造 engine 模块，支持 from engine import sensors, devices
    engine = types.ModuleType('engine')
    engine.sensors = sensors
    engine.devices = devices

    _real_import = _bi.__import__
    custom_import = _make_custom_import(engine, _real_import)
    restricted_builtins = _make_restricted_builtins(custom_import)

    namespace = {
        '__builtins__': restricted_builtins,
        'engine': engine,
        'Optional': Optional,
    }

    try:
        script = rule.script.strip()
        exec(compile(script, f'<rule:{rule.name}>', 'exec'), namespace)

        # 优先：查找带 loop() 方法的控制器类
        controller_cls = _find_controller_class(namespace)
        if controller_cls is not None:
            controller = controller_cls()
            ret = controller.loop()
            return bool(ret) if ret is not None else False

        # 备选：查找顶层 loop() 函数
        loop_fn = _find_loop_function(namespace)
        if loop_fn is not None:
            ret = loop_fn()
            return bool(ret) if ret is not None else False

        raise AutomationRuleExecutionError(
            "未找到带 loop() 的控制器类或顶层 loop() 函数"
        )

    except ImportError as e:
        logger.error("自动化规则 [%s] 尝试导入受限模块: %s", rule.name, e)
        raise
    except Exception as e:
        logger.exception("自动化规则执行异常 [%s]: %s", rule.name, e)
        raise


def _find_controller_class(namespace: dict):
    """从命名空间查找第一个带 loop() 方法的类（非内置）"""
    for name, obj in namespace.items():
        if (isinstance(obj, type)
                and not name.startswith('_')
                and hasattr(obj, 'loop')
                and callable(getattr(obj, 'loop'))):
            return obj
    return None


def _find_loop_function(namespace: dict):
    """从命名空间查找顶层 loop 函数（非类，非内置）"""
    fn = namespace.get('loop')
    if fn is not None and callable(fn) and not isinstance(fn, type):
        return fn
    return None
