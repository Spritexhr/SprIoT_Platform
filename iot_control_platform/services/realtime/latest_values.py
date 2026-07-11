"""
进程内最新点位值缓存。

设计要点
- LatestValuesCache 仅在单进程内有效，存"最近一帧"，用于快照/回填。
- 多进程下每个 worker 各自维护一份缓存，每条新样本会通过 channel layer
  广播到所有 worker；缓存只用于 snapshot 端点和 consumer 建连首发，
  接受短暂的"miss → 等下一帧"。
- 写入方（插件 signal handler）调用 ingest_sensor_data(...)，更新缓存
  并通过 services.realtime.dispatch.publish_plugin_sample 广播到 channel layer。
- 主模型(Sensor/Device)对本模块零依赖；展示元数据由插件 binding 对象按鸭子类型传入。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class PointSample:
    """单个监测点的最新值。"""

    sensor_id: str
    plugin_code: str
    tag: str  # 仪表位号，例如 TT-101
    value: Optional[float]
    unit: str = ""
    ts: Optional[float] = None
    status: str = "normal"  # normal / warn_high / warn_low / alarm_high / alarm_low
    # 在线状态：与传感器管理页同一套口径（Sensor.computed_is_online，last_seen 3 分钟内）。
    # None 表示该点位没有对应的主模型 Sensor 可查（非鸭子类型 binding），前端按 ts 兜底判断。
    is_online: Optional[bool] = None
    last_seen: Optional[float] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class LatestValuesCache:
    """线程安全的最新点位值缓存，按 sensor_id 索引。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: Dict[str, PointSample] = {}

    def update(self, sample: PointSample) -> None:
        with self._lock:
            self._values[sample.sensor_id] = sample

    def get(self, sensor_id: str) -> Optional[PointSample]:
        with self._lock:
            return self._values.get(sensor_id)

    def snapshot(self, plugin_code: Optional[str] = None) -> List[PointSample]:
        with self._lock:
            if plugin_code is None:
                return list(self._values.values())
            return [s for s in self._values.values() if s.plugin_code == plugin_code]

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


def classify_status(value: Optional[float], hi: Any, lo: Any, severity: str) -> str:
    """根据 binding 上的阈值判定状态。"""
    if value is None:
        return "normal"
    try:
        sev = (severity or "mid").lower()
        if hi is not None and value > float(hi):
            return "alarm_high" if sev in ("high", "critical") else "warn_high"
        if lo is not None and value < float(lo):
            return "alarm_low" if sev in ("high", "critical") else "warn_low"
    except (TypeError, ValueError):
        pass
    return "normal"


latest_values = LatestValuesCache()


def build_point_sample(
    sensor_id: str,
    data: dict,
    timestamp: Optional[float],
    *,
    plugin_code: str,
    binding: Any,
) -> PointSample:
    """
    把一次传感器数据上报转成 PointSample（纯构造，不入缓存、不广播）。

    供两类调用方复用：
      - ingest_sensor_data（插件层，如 EB）：构造后写全局缓存 + 广播到 plugins.{code}
      - projects 层：构造后走自己的缓存/广播（projects.{id}），避免与插件共享全局缓存互相覆盖

    binding 是调用方自有的绑定对象（鸭子类型），需具备以下属性：
      tag / unit / data_key / hi_threshold / lo_threshold / severity /
      area (可选) / normal_value (可选) / sensor (可选，FK 到主模型 Sensor，
      用于按 Sensor.computed_is_online 口径算在线状态，没有就留空让前端按 ts 兜底)
    """
    tag = getattr(binding, "tag", "") or sensor_id
    unit = getattr(binding, "unit", "") or ""
    data_key = getattr(binding, "data_key", "") or ""
    hi = getattr(binding, "hi_threshold", None)
    lo = getattr(binding, "lo_threshold", None)
    severity = getattr(binding, "severity", "mid") or "mid"

    sensor = getattr(binding, "sensor", None)
    is_online = bool(sensor.computed_is_online) if sensor is not None else None
    last_seen = sensor.last_seen.timestamp() if sensor is not None and sensor.last_seen else None

    value: Optional[float] = None
    if isinstance(data, dict):
        if data_key and data_key in data:
            raw = data[data_key]
        elif len(data) == 1:
            raw = next(iter(data.values()))
        else:
            raw = data.get("value") or data.get("temperature") or data.get("pressure") \
                or data.get("flow_rate") or data.get("level")
        try:
            value = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            value = None

    sample = PointSample(
        sensor_id=sensor_id,
        plugin_code=plugin_code or "",
        tag=tag,
        value=value,
        unit=unit,
        # 无历史数据的占位样本必须保留空时间戳；若伪造成当前时间，
        # 前端按时间兜底时会把从未上报过数据的传感器误判为在线。
        ts=float(timestamp) if timestamp is not None else None,
        status=classify_status(value, hi, lo, severity),
        is_online=is_online,
        last_seen=last_seen,
        metadata={
            "area": getattr(binding, "area", "") or "",
            "description": getattr(binding, "description", "") or "",
            "normal_value": getattr(binding, "normal_value", None),
            "hi_threshold": hi,
            "lo_threshold": lo,
            "severity": severity,
        },
    )
    return sample


def ingest_sensor_data(
    sensor_id: str,
    data: dict,
    timestamp: Optional[float],
    *,
    plugin_code: str,
    binding: Any,
) -> Optional[PointSample]:
    """
    构造 PointSample → 写全局缓存 → 广播到 plugins.{plugin_code} group。
    插件层（如 EB）使用；projects 层不走此函数（用 build_point_sample + 自有缓存/广播，
    避免与插件共享全局缓存按 point_id 互相覆盖）。
    """
    sample = build_point_sample(
        sensor_id, data, timestamp, plugin_code=plugin_code, binding=binding,
    )
    latest_values.update(sample)
    # 广播到 channel layer 的 plugins.{plugin_code} group；
    # 延迟 import 避免顶层循环（dispatch → channels → settings 链路较长）
    from . import dispatch
    dispatch.publish_plugin_sample(plugin_code or "", sample.to_dict())
    return sample
