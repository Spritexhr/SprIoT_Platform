"""
节点参数 schema —— 描述节点类的可配置构造参数

每个节点类通过 PARAMS_SCHEMA 类属性声明自己的构造参数（List[ParamSpec]），用途：
  1. run.py 加载 manifest 时聚合校验，错误信息能定位到"哪个节点哪个字段为什么错"
  2. webui /api/meta/modules 输出 JSON，前端按 schema 动态渲染节点编辑表单

ParamSpec.type 取值：
  int / float / str / bool   基本类型（int/float 拒绝 bool，避免 YAML true 被当 1）
  waveform_map               {数据字段名: 波形配置}，逐字段按 WAVEFORM_SCHEMAS 校验
  fields_map                 generic_sensor 的 {字段名: {waveform, precision, unit}}
  state_fields               generic_device 的 {字段名: {type, initial, min, max}}
  dict                       自由字典（仅类型检查，不深入校验，如 pin_control 的 initial_levels）

节点类还可以声明：
  LABEL               GUI 展示名（如 "DHT11 温湿度传感器"）
  SUPPORTED_COMMANDS  支持的控制命令列表（含参数说明），供 GUI 渲染快捷命令按钮
"""
import math
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from common.waveforms import validate_waveform_config

# manifest entry 中由 run.py 显式处理、不进 PARAMS_SCHEMA 的字段
RESERVED_ENTRY_KEYS = {"module", "id", "username", "password"}


@dataclass
class ParamSpec:
    name: str
    type: str                      # int / float / str / bool / waveform_map / fields_map / state_fields / dict
    label: str = ""
    default: Any = None
    required: bool = False
    min: Optional[float] = None
    max: Optional[float] = None
    choices: Optional[list] = None
    help: str = ""
    # waveform_map 专用：本节点上报的数据字段名（GUI 据此渲染"字段 × 波形"复合表单）
    fields: Optional[List[str]] = None

    def to_json(self) -> dict:
        out = {"name": self.name, "type": self.type, "label": self.label or self.name}
        for k in ("default", "min", "max", "choices", "help", "fields"):
            v = getattr(self, k)
            if v is not None and v != "":
                out[k] = v
        if self.required:
            out["required"] = True
        return out


def _is_number(v) -> bool:
    return (
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(v)
    )


def _check_scalar(spec: ParamSpec, value, path: str) -> List[str]:
    """基本类型 + 范围检查，返回错误列表"""
    t = spec.type
    if t == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"{path}: 应为整数，收到 {value!r}"]
    elif t == "float":
        if not _is_number(value):
            return [f"{path}: 应为数字，收到 {value!r}"]
    elif t == "str":
        if not isinstance(value, str):
            return [f"{path}: 应为字符串，收到 {value!r}"]
    elif t == "bool":
        if not isinstance(value, bool):
            return [f"{path}: 应为布尔值 true/false，收到 {value!r}"]

    errors = []
    if t in ("int", "float"):
        if spec.min is not None and value < spec.min:
            errors.append(f"{path}: 应 ≥ {spec.min}，收到 {value!r}")
        if spec.max is not None and value > spec.max:
            errors.append(f"{path}: 应 ≤ {spec.max}，收到 {value!r}")
    if spec.choices and value not in spec.choices:
        errors.append(f"{path}: 应为 {spec.choices} 之一，收到 {value!r}")
    return errors


def _check_waveform_map(value, path: str, allowed_fields: Optional[List[str]]) -> List[str]:
    if not isinstance(value, dict):
        return [f"{path}: 应为 {{字段名: 波形配置}} 字典，收到 {value!r}"]
    errors = []
    for field_name, wf_cfg in value.items():
        if allowed_fields and field_name not in allowed_fields:
            errors.append(
                f"{path}.{field_name}: 本节点没有该数据字段，可用: {'/'.join(allowed_fields)}"
            )
            continue
        errors.extend(validate_waveform_config(wf_cfg, path=f"{path}.{field_name}"))
    return errors


def _check_fields_map(value, path: str) -> List[str]:
    """generic_sensor 的 fields: {字段名: {waveform: {...}, precision: int, unit: str}}"""
    if not isinstance(value, dict) or not value:
        return [f"{path}: 应为非空的 {{字段名: 字段配置}} 字典，收到 {value!r}"]
    errors = []
    for field_name, cfg in value.items():
        p = f"{path}.{field_name}"
        if not isinstance(cfg, dict):
            errors.append(f"{p}: 字段配置应为字典，收到 {cfg!r}")
            continue
        unknown = set(cfg) - {"waveform", "precision", "unit"}
        if unknown:
            errors.append(f"{p}: 不支持的键 {sorted(unknown)}，可用: waveform/precision/unit")
        if "waveform" not in cfg:
            errors.append(f"{p}: 缺少 waveform 配置")
        else:
            errors.extend(validate_waveform_config(cfg["waveform"], path=f"{p}.waveform"))
        precision = cfg.get("precision")
        if precision is not None and (
            isinstance(precision, bool) or not isinstance(precision, int)
            or not 0 <= precision <= 6
        ):
            errors.append(f"{p}: precision 应为 0-6 的整数，收到 {precision!r}")
        unit = cfg.get("unit")
        if unit is not None and not isinstance(unit, str):
            errors.append(f"{p}: unit 应为字符串，收到 {unit!r}")
    return errors


def _check_state_fields(value, path: str) -> List[str]:
    """generic_device 的 state_fields: {字段名: {type: bool|float, initial, min, max}}"""
    if not isinstance(value, dict) or not value:
        return [f"{path}: 应为非空的 {{字段名: 状态配置}} 字典，收到 {value!r}"]
    errors = []
    for field_name, cfg in value.items():
        p = f"{path}.{field_name}"
        if not isinstance(cfg, dict):
            errors.append(f"{p}: 状态配置应为字典，收到 {cfg!r}")
            continue
        unknown = set(cfg) - {"type", "initial", "min", "max"}
        if unknown:
            errors.append(f"{p}: 不支持的键 {sorted(unknown)}，可用: type/initial/min/max")
        f_type = cfg.get("type", "float")
        if f_type not in ("bool", "float"):
            errors.append(f"{p}: type 应为 bool 或 float，收到 {f_type!r}")
            continue
        initial = cfg.get("initial")
        if f_type == "bool":
            if initial is not None and not isinstance(initial, bool):
                errors.append(f"{p}: bool 字段的 initial 应为 true/false，收到 {initial!r}")
            if "min" in cfg or "max" in cfg:
                errors.append(f"{p}: bool 字段不支持 min/max")
        else:
            for k in ("initial", "min", "max"):
                v = cfg.get(k)
                if v is not None and not _is_number(v):
                    errors.append(f"{p}: {k} 应为数字，收到 {v!r}")
            lo, hi = cfg.get("min"), cfg.get("max")
            if _is_number(lo) and _is_number(hi) and lo > hi:
                errors.append(f"{p}: min ({lo}) 不能大于 max ({hi})")
    return errors


def validate_params(node_cls, params: dict) -> Tuple[List[str], List[str]]:
    """
    按 node_cls.PARAMS_SCHEMA 校验构造参数（不含 module/id/username/password）。

    返回 (errors, warnings)：
      errors   必填缺失、类型/范围错误、波形配置错误 —— 应阻止启动
      warnings schema 之外的未知参数 —— 仍透传给构造函数，保持灵活性
    """
    schema: List[ParamSpec] = getattr(node_cls, "PARAMS_SCHEMA", [])
    specs = {s.name: s for s in schema}
    errors: List[str] = []
    warnings: List[str] = []

    for key in params:
        if key not in specs:
            warnings.append(f"{key}: 不在参数 schema 中（仍会透传给节点构造函数）")

    for name, spec in specs.items():
        if name not in params:
            if spec.required:
                errors.append(f"{name}: 缺少必填参数")
            continue
        value = params[name]
        if spec.type == "waveform_map":
            errors.extend(_check_waveform_map(value, name, spec.fields))
        elif spec.type == "fields_map":
            errors.extend(_check_fields_map(value, name))
        elif spec.type == "state_fields":
            errors.extend(_check_state_fields(value, name))
        elif spec.type == "dict":
            if not isinstance(value, dict):
                errors.append(f"{name}: 应为字典，收到 {value!r}")
        else:
            errors.extend(_check_scalar(spec, value, name))

    return errors, warnings


def validate_entry(node_cls, entry: dict) -> Tuple[List[str], List[str]]:
    """校验一条 manifest 节点 entry（剔除 reserved 字段后走 validate_params）"""
    params = {k: v for k, v in entry.items() if k not in RESERVED_ENTRY_KEYS}
    return validate_params(node_cls, params)


def module_meta(module_name: str, node_cls) -> dict:
    """节点类 → /api/meta/modules 的一条 JSON 描述"""
    return {
        "module": module_name,
        "node_type": node_cls.NODE_TYPE,
        "label": getattr(node_cls, "LABEL", module_name),
        "doc": (node_cls.__doc__ or "").strip().split("\n")[0],
        "params_schema": [s.to_json() for s in getattr(node_cls, "PARAMS_SCHEMA", [])],
        "supported_commands": getattr(node_cls, "SUPPORTED_COMMANDS", []),
    }
