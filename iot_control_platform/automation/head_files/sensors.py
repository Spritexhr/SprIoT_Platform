"""
自动化脚本可用的 sensors 模块
提供 sensors.get(device_id) 获取传感器包装对象，含：
  .current_state   — 最新 SensorData.data（首次访问时查询，本次循环内缓存）
  .refresh()       — 从数据库重新读取，刷新缓存
  .history(field, n=10)      — 最近 n 条字段值（时间升序，最新在最后）
  .average(field, minutes=5) — 最近 N 分钟内字段均值（无数据时返回 None）
  .is_online       — 是否在线（3 分钟内有数据上报）
  .model           — 原始 Sensor 模型实例
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SensorWrapper:
    """传感器包装对象，每次执行周期构建一次；current_state 首次访问时惰性加载。"""

    def __init__(self, sensor_id: str, sensor_model):
        self.device_id = sensor_id
        self._sensor = sensor_model
        self._state_cache: Optional[dict] = None

    @property
    def model(self):
        return self._sensor

    @property
    def current_state(self) -> dict:
        """最新一条 SensorData.data（本次循环内缓存，调用 refresh() 可强制重读）"""
        if self._state_cache is None:
            self.refresh()
        return self._state_cache

    def refresh(self) -> dict:
        """从数据库重新读取最新状态，刷新缓存并返回"""
        if self._sensor:
            latest = self._sensor.data_records.order_by(
                '-received_at', '-pk'
            ).first()
            self._state_cache = latest.data if latest and latest.data else {}
        else:
            self._state_cache = {}
        return self._state_cache

    def history(self, field: str, n: int = 10) -> List:
        """
        返回最近 n 条该字段的值（时间升序，最新在最后）。
        某条记录不含该字段时跳过，不会填 None。
        """
        if not self._sensor:
            return []
        records = list(
            self._sensor.data_records.order_by('-received_at', '-pk')[:n]
        )
        values = []
        for rec in reversed(records):
            val = rec.data.get(field) if rec.data else None
            if val is not None:
                values.append(val)
        return values

    def average(self, field: str, minutes: int = 5) -> Optional[float]:
        """
        返回最近 N 分钟内该字段的平均值（float）。
        无数据或全部无法转换为数字时返回 None。
        """
        if not self._sensor:
            return None
        from django.utils import timezone
        from datetime import timedelta
        threshold = timezone.now() - timedelta(minutes=minutes)
        records = self._sensor.data_records.filter(received_at__gte=threshold)
        values = []
        for rec in records:
            val = rec.data.get(field) if rec.data else None
            try:
                values.append(float(val))
            except (TypeError, ValueError):
                pass
        return sum(values) / len(values) if values else None

    @property
    def is_online(self) -> bool:
        """是否在线：3 分钟内有数据上报"""
        return bool(self._sensor and self._sensor.computed_is_online)


def build_sensors(device_list: List[Dict]) -> Any:
    """
    根据 device_list 构建 sensors 对象，供脚本通过 sensors.get(device_id) 使用。
    """
    from sensors.models import Sensor

    registry: Dict[str, SensorWrapper] = {}

    if not isinstance(device_list, list):
        return _make_sensors_proxy(registry)

    for item in device_list:
        if not isinstance(item, dict):
            continue
        device_id = item.get('device_id')
        device_type_str = (item.get('device_type') or '').strip()
        if not device_id or device_type_str.lower() != 'sensor':
            continue

        try:
            sensor = Sensor.objects.get(sensor_id=device_id)
            registry[device_id] = SensorWrapper(device_id, sensor)
        except Sensor.DoesNotExist:
            logger.warning("自动化规则：未找到传感器 %s", device_id)
            registry[device_id] = SensorWrapper(device_id, None)

    return _make_sensors_proxy(registry)


def _make_sensors_proxy(registry: Dict[str, SensorWrapper]) -> Any:
    """构造具有 get(device_id) 方法的 sensors 代理对象"""
    def get(_self, device_id: str) -> Optional[SensorWrapper]:
        return registry.get(device_id)

    return type('Sensors', (), {'get': get})()
