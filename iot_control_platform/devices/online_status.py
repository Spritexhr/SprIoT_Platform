"""设备在线判定的统一运行时配置。"""
import logging
import threading
import time

from config.platform_config import get_config

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_OFFLINE_TIMEOUT = 300
MAX_DEVICE_OFFLINE_TIMEOUT = 86400
_CACHE_SECONDS = 5.0
_cache_lock = threading.Lock()
_cached_value = DEFAULT_DEVICE_OFFLINE_TIMEOUT
_cache_expires_at = 0.0


def clear_device_offline_timeout_cache() -> None:
    """配置 reload 或测试修改配置后主动使本进程缓存失效。"""
    global _cache_expires_at
    with _cache_lock:
        _cache_expires_at = 0.0


def get_device_offline_timeout() -> int:
    """读取统一的离线阈值，并用短 TTL 避免 MQTT/列表热路径反复查库。"""
    global _cached_value, _cache_expires_at

    now = time.monotonic()
    if now < _cache_expires_at:
        return _cached_value

    with _cache_lock:
        now = time.monotonic()
        if now < _cache_expires_at:
            return _cached_value
        value = get_config(
            "device_offline_timeout",
            DEFAULT_DEVICE_OFFLINE_TIMEOUT,
            int,
        )
        if not isinstance(value, int) or value < 1 or value > MAX_DEVICE_OFFLINE_TIMEOUT:
            logger.warning(
                "device_offline_timeout=%r 无效，回退到 %s 秒",
                value,
                DEFAULT_DEVICE_OFFLINE_TIMEOUT,
            )
            value = DEFAULT_DEVICE_OFFLINE_TIMEOUT
        _cached_value = value
        _cache_expires_at = now + _CACHE_SECONDS
        return value
