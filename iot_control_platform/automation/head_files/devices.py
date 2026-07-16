"""
自动化脚本可用的 devices 模块
提供 devices.get(device_id) 获取设备包装对象，含：
  .current_state              — 最新 DeviceStatusCollection.data（首次访问时查询，本次循环内缓存）
  .refresh()                  — 从数据库重新读取，刷新缓存
  .send_command(name, params) — 发送控制命令，不等待设备确认
  .send_command_with_make_sure(name, params, timeout=3)
                              — 发送控制命令并等待设备确认
  .is_online                  — 是否在线（按平台 device_offline_timeout 判定）
  .model                      — 原始 Device 模型实例
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DeviceWrapper:
    """设备包装对象，每次执行周期构建一次；current_state 首次访问时惰性加载。"""

    def __init__(self, device_id: str, device_model, send_command_fn,
                 send_command_with_make_sure_fn):
        self.device_id = device_id
        self._device = device_model
        self._send_command_fn = send_command_fn
        self._send_command_with_make_sure_fn = send_command_with_make_sure_fn
        self._state_cache: Optional[dict] = None

    @property
    def model(self):
        return self._device

    @property
    def current_state(self) -> dict:
        """最新一条 DeviceStatusCollection.data（本次循环内缓存，调用 refresh() 可强制重读）"""
        if self._state_cache is None:
            self.refresh()
        return self._state_cache

    def refresh(self) -> dict:
        """从数据库重新读取最新状态，刷新缓存并返回"""
        if self._device:
            latest = self._device.status_records.order_by(
                '-received_at', '-pk'
            ).first()
            self._state_cache = latest.data if latest and latest.data else {}
        else:
            self._state_cache = {}
        return self._state_cache

    def send_command(self, name: str, params: Optional[Dict] = None) -> Any:
        """发送控制命令，只确认 MQTT 发布是否成功，不等待设备回传。"""
        return self._send_command_fn(name, params)

    def send_command_with_make_sure(
        self,
        name: str,
        params: Optional[Dict] = None,
        timeout: int = 3,
    ) -> Any:
        """发送控制命令，并等待设备回传 check_code 确认。"""
        from services.mqtt_command_bus import (
            MAX_DEVICE_ACK_TIMEOUT_SECONDS,
            validate_command_timeout,
        )

        bounded_timeout = validate_command_timeout(
            timeout,
            name="timeout",
            maximum=MAX_DEVICE_ACK_TIMEOUT_SECONDS,
        )
        return self._send_command_with_make_sure_fn(
            name,
            params,
            bounded_timeout,
        )

    @property
    def is_online(self) -> bool:
        """使用设备模型的统一运行时离线阈值。"""
        return bool(self._device and self._device.computed_is_online)


def _make_noop_send(device_id: str):
    """为未找到的设备返回空操作命令函数。"""
    def _noop(name=None, params=None, timeout=None):
        logger.warning("自动化规则：设备 %s 不存在，忽略命令 %s", device_id, name)
        return False
    return _noop


def build_devices(device_list: List[Dict]) -> Any:
    """
    根据 device_list 构建 devices 对象，供脚本通过 devices.get(device_id) 使用。
    """
    from devices.models import Device
    from services.devices_service.device_command_send_service import device_command_send_service

    registry: Dict[str, DeviceWrapper] = {}

    if not isinstance(device_list, list):
        return _make_devices_proxy(registry)

    for item in device_list:
        if not isinstance(item, dict):
            continue
        device_id = item.get('device_id')
        device_type_str = (item.get('device_type') or '').strip()
        if not device_id or device_type_str.lower() != 'device':
            continue

        try:
            dev = Device.objects.get(device_id=device_id)

            def _make_send_cmd(did: str):
                def _send_cmd(name: str, params: Optional[Dict] = None):
                    return device_command_send_service.send_command(
                        object_id=did,
                        command_name=name,
                        params=params or {},
                    )
                return _send_cmd

            def _make_send_cmd_with_make_sure(did: str):
                def _send_cmd(name: str, params: Optional[Dict] = None, timeout: int = 3):
                    return device_command_send_service.send_command_with_make_sure(
                        object_id=did,
                        command_name=name,
                        params=params or {},
                        timeout=timeout,
                    )
                return _send_cmd

            registry[device_id] = DeviceWrapper(
                device_id,
                dev,
                _make_send_cmd(device_id),
                _make_send_cmd_with_make_sure(device_id),
            )
        except Device.DoesNotExist:
            logger.warning("自动化规则：未找到设备 %s", device_id)
            noop = _make_noop_send(device_id)
            registry[device_id] = DeviceWrapper(device_id, None, noop, noop)

    return _make_devices_proxy(registry)


def _make_devices_proxy(registry: Dict[str, DeviceWrapper]) -> Any:
    """构造具有 get(device_id) 方法的 devices 代理对象"""
    def get(_self, device_id: str) -> Optional[DeviceWrapper]:
        return registry.get(device_id)

    return type('Devices', (), {'get': get})()
