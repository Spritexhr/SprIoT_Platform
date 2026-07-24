"""
仿真数据波形生成器

提供可参数化的数据源，供各虚拟传感器读数使用。每个节点在 config.yaml 里
为每个上报字段指定一种波形，例如：

    waveforms:
      temperature: {type: sine, min: 18, max: 28, period: 3600, jitter: 0.3}
      humidity:    {type: random_walk, start: 55, step: 1.5, bounds: [40, 70]}

通过 build_waveform(cfg) 把 dict 装配成具体实例，调用 .sample() 取下一个值。
"""
import math
import random
import time
from typing import Tuple


class Waveform:
    def sample(self) -> float:
        raise NotImplementedError


class SineWave(Waveform):
    """
    正弦波，适合昼夜温度、潮汐等周期性信号。

    Args:
        min: 波形最小值
        max: 波形最大值
        period: 周期（秒）
        jitter: 在采样值上叠加的均匀噪声幅度（±jitter），默认 0
        phase: 初始相位偏移（秒），默认 0；多个同型节点可错开避免完全同步
    """
    def __init__(self, min: float, max: float, period: float,
                 jitter: float = 0.0, phase: float = 0.0):
        self.min = float(min)
        self.max = float(max)
        self.period = float(period)
        self.jitter = float(jitter)
        self._t0 = time.time() - float(phase)

    def sample(self) -> float:
        amplitude = (self.max - self.min) / 2.0
        center = (self.max + self.min) / 2.0
        elapsed = time.time() - self._t0
        v = center + amplitude * math.sin(2.0 * math.pi * elapsed / self.period)
        if self.jitter:
            v += random.uniform(-self.jitter, self.jitter)
        return v


class RandomWalk(Waveform):
    """
    随机游走，适合湿度、电池电量这种慢变量。

    Args:
        start: 初始值
        step: 每次采样的最大步长（实际步长在 ±step 间均匀分布）
        bounds: [下界, 上界]，越界后会被夹回
    """
    def __init__(self, start: float, step: float, bounds: Tuple[float, float]):
        self.value = float(start)
        self.step = float(step)
        lo, hi = bounds
        self.lo = float(lo)
        self.hi = float(hi)

    def sample(self) -> float:
        self.value += random.uniform(-self.step, self.step)
        if self.value < self.lo:
            self.value = self.lo
        elif self.value > self.hi:
            self.value = self.hi
        return self.value


class UniformRandom(Waveform):
    """[min, max] 区间内均匀分布的独立采样，无记忆。"""
    def __init__(self, min: float, max: float):
        self.min = float(min)
        self.max = float(max)

    def sample(self) -> float:
        return random.uniform(self.min, self.max)


class Constant(Waveform):
    """恒定值，适合开关类（0/1）或固定基线。"""
    def __init__(self, value: float):
        self.value = float(value)

    def sample(self) -> float:
        return self.value


WAVEFORM_REGISTRY = {
    "sine": SineWave,
    "random_walk": RandomWalk,
    "uniform": UniformRandom,
    "constant": Constant,
}


# 每种波形的参数 schema —— 三个用途：
#   1. build_waveform 装配前校验，把 TypeError 变成"哪个参数错了"的友好提示
#   2. run.py 加载 manifest 时聚合校验
#   3. webui /api/meta/waveforms 动态渲染表单 + 预览曲线
# param.type 只有两种：float（数字）/ range（[下界, 上界] 两元素数字列表）
WAVEFORM_SCHEMAS = {
    "sine": {
        "label": "正弦波",
        "help": "周期性信号，适合昼夜温度、潮汐、工艺周期变化",
        "params": [
            {"name": "min", "type": "float", "required": True, "label": "最小值"},
            {"name": "max", "type": "float", "required": True, "label": "最大值"},
            {"name": "period", "type": "float", "required": True, "min_value": 1,
             "label": "周期(秒)"},
            {"name": "jitter", "type": "float", "default": 0.0, "min_value": 0,
             "label": "噪声幅度(±)", "help": "采样值上叠加 ±jitter 的均匀噪声"},
            {"name": "phase", "type": "float", "default": 0.0,
             "label": "相位偏移(秒)", "help": "多个同型节点错开相位，避免完全同步"},
        ],
    },
    "random_walk": {
        "label": "随机游走",
        "help": "慢变量，适合湿度、电池电量、缓变压力",
        "params": [
            {"name": "start", "type": "float", "required": True, "label": "初始值"},
            {"name": "step", "type": "float", "required": True, "min_value": 0,
             "label": "最大步长", "help": "每次采样在 ±step 间均匀取步进"},
            {"name": "bounds", "type": "range", "required": True,
             "label": "取值范围 [下界, 上界]", "help": "越界后被夹回"},
        ],
    },
    "uniform": {
        "label": "均匀随机",
        "help": "区间内独立采样，无记忆",
        "params": [
            {"name": "min", "type": "float", "required": True, "label": "最小值"},
            {"name": "max", "type": "float", "required": True, "label": "最大值"},
        ],
    },
    "constant": {
        "label": "恒定值",
        "help": "固定基线或开关量(0/1)",
        "params": [
            {"name": "value", "type": "float", "required": True, "label": "值"},
        ],
    },
}


def _is_number(v) -> bool:
    return (
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(v)
    )


def validate_waveform_config(cfg, path: str = "波形") -> list:
    """
    按 WAVEFORM_SCHEMAS 校验单个波形配置，返回错误信息列表（空 = 通过）。

    path 用于错误前缀，调用方可传 "waveforms.temperature" 之类的定位串。
    """
    if not isinstance(cfg, dict) or "type" not in cfg:
        return [f"{path}: 波形配置必须是含 'type' 字段的字典，收到 {cfg!r}"]

    wf_type = cfg["type"]
    schema = WAVEFORM_SCHEMAS.get(wf_type)
    if schema is None:
        return [f"{path}: 未知波形类型 '{wf_type}'，可选: {list(WAVEFORM_SCHEMAS.keys())}"]

    specs = {p["name"]: p for p in schema["params"]}
    errors = []

    for key in cfg:
        if key != "type" and key not in specs:
            errors.append(
                f"{path}: 波形 {wf_type} 不支持参数 '{key}'，可用: {'/'.join(specs)}"
            )

    for name, spec in specs.items():
        if name not in cfg:
            if spec.get("required"):
                errors.append(f"{path}: 波形 {wf_type} 缺少必填参数 '{name}'")
            continue
        val = cfg[name]
        if spec["type"] == "range":
            if not (isinstance(val, (list, tuple)) and len(val) == 2
                    and all(_is_number(v) for v in val)):
                errors.append(
                    f"{path}: 参数 '{name}' 应为 [下界, 上界] 两元素数字列表，收到 {val!r}"
                )
            elif val[0] > val[1]:
                errors.append(f"{path}: 参数 '{name}' 下界不能大于上界: {val!r}")
        else:
            if not _is_number(val):
                errors.append(f"{path}: 参数 '{name}' 应为数字，收到 {val!r}")
            elif spec.get("min_value") is not None and val < spec["min_value"]:
                errors.append(
                    f"{path}: 参数 '{name}' 应 ≥ {spec['min_value']}，收到 {val!r}"
                )

    # 语义检查：min/max 关系
    if not errors and wf_type in ("sine", "uniform") and cfg["min"] > cfg["max"]:
        errors.append(f"{path}: min ({cfg['min']}) 不能大于 max ({cfg['max']})")

    return errors


def build_waveform(cfg: dict) -> Waveform:
    """
    把配置字典装配成 Waveform 实例。

    cfg 形如 {"type": "sine", "min": 18, "max": 28, "period": 3600}
    或简写 {"type": "constant", "value": 42}
    """
    errors = validate_waveform_config(cfg)
    if errors:
        raise ValueError("; ".join(errors))
    params = {k: v for k, v in cfg.items() if k != "type"}
    return WAVEFORM_REGISTRY[cfg["type"]](**params)


def build_waveform_map(cfg: dict) -> dict:
    """
    把 {字段名: 波形配置} 批量装配成 {字段名: Waveform 实例}
    """
    return {field: build_waveform(c) for field, c in (cfg or {}).items()}
