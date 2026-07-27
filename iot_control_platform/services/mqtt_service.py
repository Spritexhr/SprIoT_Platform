"""
MQTT服务模块
负责MQTT连接管理、主题订阅、消息接收和发送
连接参数从 platform_config 读取，支持 reload 后重连以应用新配置
支持断线自动重连（指数退避）
"""
import json
import hashlib
import logging
import os
import re
import threading
import time
from typing import Callable, Dict, Optional
import paho.mqtt.client as mqtt
from django.conf import settings
from django.db import InterfaceError, OperationalError, close_old_connections

from config.platform_config import get_config
from .sensors_service.sensor_upload_data_handlers import handle_mqtt_data_message
from .sensors_service.sensor_upload_status_handlers import handle_mqtt_status_message
from .devices_service.device_upload_status_handlers import handle_mqtt_device_status_message

logger = logging.getLogger(__name__)


class MQTTService:
    """
    MQTT服务类
    管理MQTT客户端连接、消息路由和发送
    支持自动重连（指数退避策略）
    """

    # 自动重连配置
    RECONNECT_MIN_DELAY = 1      # 最小重连延迟（秒）
    RECONNECT_MAX_DELAY = 120    # 最大重连延迟（秒）
    INBOUND_RETRY_MIN_DELAY = 1
    INBOUND_RETRY_MAX_DELAY = 30

    def __init__(self):
        """初始化MQTT服务"""
        # 订阅与命令发布必须使用两个独立 Paho network-loop。订阅 callback 会
        # 同步落库；如果复用同一 client，高频遥测会阻塞命令 PUBACK，造成
        # “平台显示超时、设备实际已执行”的假失败。
        self.client: Optional[mqtt.Client] = None
        self.publisher_client: Optional[mqtt.Client] = None
        self._subscriber_connected = False
        self._publisher_connected = False
        self.is_connected = False
        self.connection_state = "stopped"
        self.last_error = ""
        self.handlers: Dict[str, Callable] = {}  # 主题模式 -> 处理器函数
        self.pending_subscriptions = []  # 待订阅的主题列表
        self._reconnect_delay = self.RECONNECT_MIN_DELAY
        self._connected_event = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._config_fingerprint = ""
        self._retry_reconnect_pending = False
        self._inbound_retry_delay = self.INBOUND_RETRY_MIN_DELAY

    def _load_connection_config(self) -> dict:
        """一次查询读取完整配置；只有缺 key 才使用默认值。

        连接配置不能复用 ``get_config`` 的宽松兜底：数据库查询异常若被吞掉，
        runner 会误连 127.0.0.1，且错误表面上像 broker 故障。
        """
        from platform_settings.models import PlatformConfig

        defaults = {
            "mqtt_broker": "127.0.0.1",
            "mqtt_port": 1883,
            "mqtt_keepalive": 60,
            "mqtt_username": "",
            "mqtt_password": "",
        }
        rows = dict(
            PlatformConfig.objects.filter(key__in=defaults).values_list("key", "value")
        )

        def _value(key):
            value = rows.get(key, defaults[key])
            return defaults[key] if value in (None, "") and key != "mqtt_password" else value

        return {
            "broker": str(_value("mqtt_broker")),
            "port": int(_value("mqtt_port")),
            "keepalive": int(_value("mqtt_keepalive")),
            "username": str(_value("mqtt_username") or ""),
            "password": str(_value("mqtt_password") or ""),
        }

    @staticmethod
    def _fingerprint(config: dict) -> str:
        raw = json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def read_config_fingerprint(self) -> str:
        """读取数据库当前 MQTT 配置的不可逆指纹，用于漏通知自愈。"""
        close_old_connections()
        try:
            return self._fingerprint(self._load_connection_config())
        finally:
            close_old_connections()

    @property
    def applied_config_fingerprint(self) -> str:
        return self._config_fingerprint

    @staticmethod
    def _publisher_client_id(subscriber_client_id: str) -> str:
        """生成稳定且不超过 MQTT 3.1 传统 23 字节限制的发布 client_id。"""
        candidate = f"{subscriber_client_id}-pub"
        if len(candidate.encode("utf-8")) <= 23:
            return candidate
        digest = hashlib.sha256(
            subscriber_client_id.encode("utf-8")
        ).hexdigest()[:6]
        # 环境中的 client_id 通常是 ASCII；只保留短前缀并附加摘要，既稳定又
        # 避免多个 runner 截断后撞 ID。
        ascii_prefix = subscriber_client_id.encode(
            "ascii", errors="ignore"
        ).decode("ascii")[:12] or "spr-iot"
        return f"{ascii_prefix}-pub-{digest}"[:23]

    def _refresh_connection_readiness(self) -> bool:
        """只有订阅与发布通道都在线时，runner 才对外宣告可用。"""
        ready = bool(
            self.client is not None
            and self.publisher_client is not None
            and self._subscriber_connected
            and self._publisher_connected
        )
        self.is_connected = ready
        if ready:
            self.connection_state = "connected"
            self.last_error = ""
            self._connected_event.set()
        else:
            self._connected_event.clear()
        return ready

    def connect_async(self) -> bool:
        """启动 runner 的订阅与命令发布客户端，不阻塞等待首次连接。

        paho ``loop_start`` 内部使用 ``loop_forever(retry_first_connection=True)``，
        因此 broker 在进程启动时不可达也会持续按 reconnect_delay_set 自愈。
        """
        with self._lifecycle_lock:
            if self.client is not None and self.publisher_client is not None:
                return True
            if self.client is not None or self.publisher_client is not None:
                # 防御上一轮启动只完成一半的异常状态。
                self.stop()
            close_old_connections()
            try:
                try:
                    config = self._load_connection_config()
                except Exception as exc:
                    self.connection_state = "disconnected"
                    self.last_error = f"config_load_failed: {exc}"
                    logger.error("读取 MQTT 连接配置失败，稍后重试: %s", exc, exc_info=True)
                    return False
            finally:
                close_old_connections()

            client_id = (
                str(os.environ.get("MQTT_CLIENT_ID") or "").strip()
                or "spr-iot-platform-runner"
            )
            publisher_client_id = self._publisher_client_id(client_id)
            try:
                client = mqtt.Client(
                    client_id=client_id,
                    clean_session=False,
                    manual_ack=True,
                )
                publisher_client = mqtt.Client(
                    client_id=publisher_client_id,
                    clean_session=True,
                    manual_ack=False,
                )
                if config["username"]:
                    client.username_pw_set(config["username"], config["password"])
                    publisher_client.username_pw_set(
                        config["username"], config["password"]
                    )
                client.on_connect = self._on_connect
                client.on_disconnect = self._on_disconnect
                client.on_message = self._on_message
                publisher_client.on_connect = self._on_publisher_connect
                publisher_client.on_disconnect = self._on_publisher_disconnect
                client.reconnect_delay_set(
                    min_delay=self.RECONNECT_MIN_DELAY,
                    max_delay=self.RECONNECT_MAX_DELAY,
                )
                publisher_client.reconnect_delay_set(
                    min_delay=self.RECONNECT_MIN_DELAY,
                    max_delay=self.RECONNECT_MAX_DELAY,
                )
            except Exception as exc:
                self.connection_state = "disconnected"
                self.last_error = f"client_init_failed: {exc}"
                logger.error("初始化 MQTT client 失败，稍后重试: %s", exc, exc_info=True)
                return False

            self.client = client
            self.publisher_client = publisher_client
            self._subscriber_connected = False
            self._publisher_connected = False
            self.is_connected = False
            self._connected_event.clear()
            self.connection_state = "connecting"
            self.last_error = ""
            self._config_fingerprint = self._fingerprint(config)
            logger.info(
                "启动 MQTT runner 双通道客户端: %s:%s "
                "subscriber_id=%s publisher_id=%s",
                config["broker"],
                config["port"],
                client_id,
                publisher_client_id,
            )
            try:
                client.connect_async(
                    config["broker"], config["port"], config["keepalive"]
                )
                client.loop_start()
                publisher_client.connect_async(
                    config["broker"], config["port"], config["keepalive"]
                )
                publisher_client.loop_start()
            except Exception as exc:
                self.client = None
                self.publisher_client = None
                self._subscriber_connected = False
                self._publisher_connected = False
                self.is_connected = False
                self._connected_event.clear()
                for started_client in (client, publisher_client):
                    try:
                        started_client.disconnect()
                    except Exception:
                        pass
                    try:
                        started_client.loop_stop()
                    except Exception:
                        pass
                self.connection_state = "disconnected"
                self.last_error = str(exc)
                logger.error("启动 MQTT 异步连接失败: %s", exc, exc_info=True)
                return False
            return True

    def connect(self, timeout: int = 5) -> bool:
        """
        连接到MQTT服务器

        Args:
            timeout: 连接超时时间（秒）

        Returns:
            bool: 连接成功返回True
        """
        if not self.connect_async():
            return False
        connected = self.wait_until_connected(timeout=timeout)
        if not connected:
            # 兼容旧调用方仍返回 False，但绝不能停止网络循环；后台会持续重试首次连接。
            logger.warning("MQTT 首次连接在 %s 秒内未完成，后台继续重试", timeout)
        return connected

    def start(self):
        """启动MQTT服务（阻塞式循环）"""
        if self.client:
            logger.info("MQTT服务启动，开始监听消息...")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        else:
            logger.error("MQTT客户端未初始化，请先调用connect()")

    def stop(self):
        """停止MQTT服务"""
        with self._lifecycle_lock:
            client = self.client
            publisher_client = self.publisher_client
            if client is None and publisher_client is None:
                return
            # 先从共享状态摘除，旧 client 的迟到 callback 不得污染新一代状态。
            self.client = None
            self.publisher_client = None
            self._subscriber_connected = False
            self._publisher_connected = False
            self.is_connected = False
            self._connected_event.clear()
            self.connection_state = "stopped"
            for owned_client in (client, publisher_client):
                if owned_client is None:
                    continue
                try:
                    owned_client.disconnect()
                except Exception:
                    logger.debug("MQTT disconnect 失败（忽略）", exc_info=True)
                try:
                    owned_client.loop_stop()
                except Exception:
                    logger.debug("MQTT loop_stop 失败（忽略）", exc_info=True)
            self._reconnect_delay = self.RECONNECT_MIN_DELAY
            logger.info("MQTT服务已停止")

    def reconnect(self, timeout: int = 5) -> bool:
        """
        断开并重新连接，使用最新 platform_config 配置
        用于配置修改后无需重启服务即可生效
        """
        self.stop()
        return self.connect(timeout=timeout)

    def reconnect_async(self) -> bool:
        """应用数据库最新配置并立即恢复后台首连重试。"""
        self.connection_state = "reloading"
        self.stop()
        return self.connect_async()

    def _schedule_retryable_reconnect(self, failed_client) -> None:
        """让未 ACK 的 QoS1 在 persistent session 重连后由 broker 重投。

        Paho/Broker 通常不会在同一条在线连接上立即重发未手动确认的入站消息，
        因此仅“不 ACK”不足以保证数据库恢复后自动再处理。生命周期操作不能在
        Paho callback 线程内执行，使用单个后台任务在 callback 返回后重建连接。
        """
        with self._lifecycle_lock:
            if (
                failed_client is not self.client
                or self._retry_reconnect_pending
            ):
                return
            self._retry_reconnect_pending = True
            retry_delay = self._inbound_retry_delay
            self._inbound_retry_delay = min(
                retry_delay * 2,
                self.INBOUND_RETRY_MAX_DELAY,
            )

        def reconnect_after_callback():
            try:
                # 与网络断线退避分开维护；on_connect 会重置网络退避，但数据库
                # 仍可能不可用。独立指数退避避免同一未 ACK 消息触发重连风暴。
                time.sleep(retry_delay)
                with self._lifecycle_lock:
                    still_current = failed_client is self.client
                if still_current:
                    self.last_error = "temporary_database_error"
                    logger.warning(
                        "数据库暂时不可用，重建 MQTT persistent session "
                        "以触发未 ACK QoS1 消息重投"
                    )
                    self.reconnect_async()
            except Exception:
                logger.exception("MQTT 入站临时故障后的重连任务失败")
            finally:
                with self._lifecycle_lock:
                    self._retry_reconnect_pending = False

        threading.Thread(
            target=reconnect_after_callback,
            name="mqtt-inbound-retry-reconnect",
            daemon=True,
        ).start()

    def wait_until_connected(self, timeout: float = 1.0) -> bool:
        return self._connected_event.wait(timeout=max(0.0, float(timeout)))

    def subscribe(self, topic: str, qos: int = 1):
        """
        订阅MQTT主题

        Args:
            topic: MQTT主题（支持通配符 + 和 #）
            qos: 服务质量等级（0, 1, 2）
        """
        subscription = (topic, qos)
        if subscription not in self.pending_subscriptions:
            self.pending_subscriptions.append(subscription)

        client = self.client
        if not self._subscriber_connected or client is None:
            logger.debug(f"MQTT未连接，主题 {topic} 将在连接后自动订阅")
            return

        try:
            result, mid = client.subscribe(topic, qos=qos)
            if result == mqtt.MQTT_ERR_SUCCESS:
                logger.info(f"已订阅主题: {topic} (QoS={qos})")
            else:
                logger.error(f"订阅主题失败: {topic}, 错误码: {result}")
        except Exception as e:
            logger.error(f"订阅主题异常: {topic}, {e}", exc_info=True)

    def publish(self, topic: str, payload: dict, qos: int = 1) -> bool:
        """
        发布消息到MQTT主题

        Args:
            topic: MQTT主题
            payload: 消息内容（字典，会自动转JSON）
            qos: 服务质量等级

        Returns:
            bool: 发布成功返回True
        """
        success, _error = self.publish_wait(topic, payload, qos=qos, timeout=2.0)
        return success

    def publish_wait(
        self,
        topic: str,
        payload: dict,
        *,
        qos: int = 1,
        timeout: float = 2.0,
    ) -> tuple[bool, str]:
        """发布并等待 QoS PUBACK。

        返回成功仅表示 broker 已确认接收；设备执行确认由 Redis command bus 的
        check_code 状态机负责。
        """
        # reload/stop 与一个最多数秒的 PUBACK 等待串行，避免正在发布时替换
        # client 导致调用方收到模糊的跨代 callback。
        with self._lifecycle_lock:
            return self._publish_wait_locked(
                topic, payload, qos=qos, timeout=timeout
            )

    def _publish_wait_locked(
        self,
        topic: str,
        payload: dict,
        *,
        qos: int,
        timeout: float,
    ) -> tuple[bool, str]:
        client = self.publisher_client
        if (
            not self.is_connected
            or client is None
            or not self._publisher_connected
            or not self._connected_event.is_set()
        ):
            logger.error("MQTT未连接，无法发布消息到: %s", topic)
            return False, "not_connected"
        if not topic or not isinstance(payload, dict):
            return False, "invalid_message"
        try:
            message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            logger.error("MQTT 命令无法序列化 topic=%s err=%s", topic, exc)
            return False, "invalid_message"
        try:
            result = client.publish(topic, message, qos=qos)
        except ValueError as exc:
            logger.error("MQTT 命令参数无效 topic=%s err=%s", topic, exc)
            return False, "invalid_message"
        except Exception as exc:
            # publish() 进入 Paho 后再抛异常时，无法可靠证明消息未进入其
            # QoS1 队列；按“交付未知”处理，避免诱导调用方立即重复执行。
            logger.error("MQTT publish 调用异常 topic=%s err=%s", topic, exc, exc_info=True)
            return False, "publish_call_exception"

        try:
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.error("MQTT publish 入队失败 topic=%s rc=%s", topic, result.rc)
                return False, f"publish_rc_{result.rc}"
            result.wait_for_publish(timeout=max(0.1, float(timeout)))
            if not result.is_published():
                logger.error("等待 MQTT broker PUBACK 超时 topic=%s", topic)
                return False, "puback_timeout"
            logger.info("MQTT broker 已确认命令 topic=%s", topic)
            logger.debug("MQTT 命令内容 topic=%s payload=%s", topic, message)
            return True, ""
        except Exception as exc:
            # 已得到 MQTTMessageInfo，消息可能已写入 socket 或仍在 Paho
            # inflight 队列，任何等待异常都不能被表述成确定失败。
            logger.error("等待 MQTT PUBACK 异常 topic=%s err=%s", topic, exc, exc_info=True)
            return False, "puback_wait_exception"

    def register_handler(self, topic_pattern: str, handler: Callable):
        """
        注册消息处理器

        Args:
            topic_pattern: 主题模式（支持通配符 + 和 #）
            handler: 处理函数，签名为 handler(topic, payload)
        """
        self.handlers[topic_pattern] = handler
        logger.info(f"已注册处理器: {topic_pattern} -> {handler.__name__}")

    def setup_sensor_data_handler(self):
        """
        快速设置传感器数据处理器
        自动注册处理器并订阅数据主题
        """
        topic = settings.MQTT_TOPICS['SENSOR_DATA']
        self.register_handler(topic, handle_mqtt_data_message)
        self.subscribe(topic, qos=1)
        logger.info("传感器数据处理器设置完成")

    def setup_sensor_status_handler(self):
        """
        快速设置传感器状态处理器
        自动注册处理器并订阅状态主题
        """
        topic = settings.MQTT_TOPICS['SENSOR_STATUS']
        self.register_handler(topic, handle_mqtt_status_message)
        self.subscribe(topic, qos=1)
        logger.info("传感器状态处理器设置完成")

    def setup_device_status_handler(self):
        """
        快速设置设备状态处理器
        自动注册处理器并订阅设备状态主题
        """
        self.register_handler('iot/devices/+/status', handle_mqtt_device_status_message)
        self.subscribe('iot/devices/+/status', qos=1)
        logger.info("设备状态处理器设置完成")

    def _publish_system_status(self, extra: Optional[dict] = None) -> None:
        """把当前 MQTT 连接状态推到 /ws/system/mqtt/ 订阅者。"""
        close_old_connections()
        try:
            from services.realtime.dispatch import publish_mqtt_system
            payload = {
                "is_connected": bool(self.is_connected),
                "broker": get_config("mqtt_broker", "127.0.0.1", str),
                "port": get_config("mqtt_port", 1883, int),
            }
            if extra:
                payload.update(extra)
            publish_mqtt_system(payload)
        except Exception as exc:
            # 不能让推 WS 的异常影响 MQTT 回调，吞掉
            logger.debug(f"MQTT 状态广播失败（忽略）: {exc}")
        finally:
            close_old_connections()

    def _on_connect(self, client, userdata, flags, rc):
        """订阅通道连接回调。"""
        if client is not self.client:
            logger.debug("忽略旧 MQTT client 的 on_connect callback")
            return
        if rc == 0:
            self._subscriber_connected = True
            # 连接成功，重置重连延迟
            self._reconnect_delay = self.RECONNECT_MIN_DELAY
            logger.info("MQTT 订阅通道连接成功")

            if self.pending_subscriptions:
                logger.info(f"开始订阅 {len(self.pending_subscriptions)} 个主题...")
                for topic, qos in self.pending_subscriptions:
                    try:
                        result, mid = client.subscribe(topic, qos=qos)
                        if result == mqtt.MQTT_ERR_SUCCESS:
                            logger.info(f"已订阅主题: {topic} (QoS={qos})")
                        else:
                            logger.error(f"订阅主题失败: {topic}, 错误码: {result}")
                    except Exception as e:
                        logger.error(f"订阅主题异常: {topic}, {e}", exc_info=True)
            ready = self._refresh_connection_readiness()
            self._publish_system_status(
                {
                    "subscriber_connected": True,
                    "publisher_connected": self._publisher_connected,
                    "is_connected": ready,
                }
            )
        else:
            self._subscriber_connected = False
            self._refresh_connection_readiness()
            self.connection_state = "disconnected"
            error_messages = {
                1: "协议版本错误",
                2: "客户端ID无效",
                3: "服务器不可用",
                4: "用户名或密码错误",
                5: "未授权"
            }
            error_msg = error_messages.get(rc, f"未知错误(rc={rc})")
            self.last_error = error_msg
            logger.error(f"MQTT连接失败: {error_msg}")
            self._publish_system_status({"error": error_msg, "rc": rc})

    def _on_publisher_connect(self, client, userdata, flags, rc):
        """命令发布通道连接回调；该 loop 不运行任何入站 ORM handler。"""
        if client is not self.publisher_client:
            logger.debug("忽略旧 MQTT publisher 的 on_connect callback")
            return
        if rc == 0:
            self._publisher_connected = True
            ready = self._refresh_connection_readiness()
            logger.info("MQTT 命令发布通道连接成功")
            self._publish_system_status({
                "subscriber_connected": self._subscriber_connected,
                "publisher_connected": True,
                "is_connected": ready,
            })
            return

        self._publisher_connected = False
        self._refresh_connection_readiness()
        self.connection_state = "disconnected"
        error_msg = f"命令发布通道连接失败(rc={rc})"
        self.last_error = error_msg
        logger.error(error_msg)
        self._publish_system_status({
            "error": error_msg,
            "rc": rc,
            "publisher_connected": False,
        })

    def _on_disconnect(self, client, userdata, rc):
        """
        MQTT断开连接回调。
        rc=7 表示连接丢失，常见于 client_id 冲突或网络波动。
        paho-mqtt 的 reconnect_delay_set 已启用内置自动重连，
        此处仅记录日志和更新状态。
        """
        if client is not self.client:
            return
        self._subscriber_connected = False
        self._refresh_connection_readiness()
        self.connection_state = "disconnected"
        if rc != 0:
            hint = "（连接丢失，多为 client_id 冲突或网络问题）" if rc == 7 else ""
            logger.warning(
                f"MQTT意外断开，错误码: {rc}{hint}，"
                f"paho-mqtt 内置自动重连将在 {self._reconnect_delay}s 后尝试..."
            )
            # 指数退避：下次重连延迟加倍（上限由 paho-mqtt 管理）
            self._reconnect_delay = min(self._reconnect_delay * 2, self.RECONNECT_MAX_DELAY)
            self.last_error = f"disconnect_rc_{rc}"
        else:
            logger.info("MQTT正常断开")
        self._publish_system_status({"rc": rc})

    def _on_publisher_disconnect(self, client, userdata, rc):
        """命令发布通道断开回调。"""
        if client is not self.publisher_client:
            return
        self._publisher_connected = False
        self._refresh_connection_readiness()
        self.connection_state = "disconnected"
        if rc != 0:
            self.last_error = f"publisher_disconnect_rc_{rc}"
            logger.warning(
                "MQTT 命令发布通道意外断开 rc=%s，等待 Paho 自动重连",
                rc,
            )
        else:
            logger.info("MQTT 命令发布通道正常断开")
        self._publish_system_status({
            "rc": rc,
            "publisher_connected": False,
        })

    def _extract_id_from_topic(self, topic: str, pattern: str) -> Optional[str]:
        """
        从主题中提取设备ID或传感器ID。
        如 iot/sensors/DHT11-WEMOS-001/data -> DHT11-WEMOS-001
        iot/devices/SG_80_01/status -> SG_80_01
        """
        parts = topic.split('/')
        if len(parts) >= 3:
            return parts[2]  # iot / sensors|devices / ID / ...
        return None

    def _on_message(self, client, userdata, msg):
        """处理入站 QoS1；成功落库或持久化死信后才手动 ACK。"""
        topic = msg.topic
        raw_payload = bytes(getattr(msg, "payload", b"") or b"")
        max_payload_bytes = max(
            1,
            int(getattr(settings, "MQTT_INBOUND_MAX_PAYLOAD_BYTES", 256 * 1024)),
        )
        if len(raw_payload) > max_payload_bytes:
            reason = (
                "payload_too_large: "
                f"{len(raw_payload)} bytes exceeds {max_payload_bytes}"
            )
            logger.error("MQTT 入站消息过大，拒绝解析 topic=%s %s", topic, reason)
            self._dead_letter_or_leave_unacked(client, msg, reason)
            return
        # Paho 网络线程不经过 Django request_started/request_finished；主动清理
        # thread-local DB connection，避免 MySQL wait_timeout 后永久复用坏连接。
        close_old_connections()
        processed = False
        retryable_failure = False
        failure_reason = "processing_failed"
        try:
            payload = json.loads(raw_payload.decode('utf-8'))
            # 提取并显示具体 ID（传感器或设备）
            extracted_id = None
            if '/sensors/' in topic:
                extracted_id = self._extract_id_from_topic(topic, 'iot/sensors/+/')
                payload_id = payload.get('sensor_id', '') if isinstance(payload, dict) else ''
                id_display = payload_id or extracted_id or '未知'
                logger.info(f"收到消息 - 主题: {topic} [传感器ID: {id_display}]")
            elif '/devices/' in topic:
                extracted_id = self._extract_id_from_topic(topic, 'iot/devices/+/')
                payload_id = payload.get('device_id', '') if isinstance(payload, dict) else ''
                id_display = payload_id or extracted_id or '未知'
                logger.info(f"收到消息 - 主题: {topic} [设备ID: {id_display}]")
            else:
                logger.info(f"收到消息 - 主题: {topic}")
            logger.debug(f"  消息内容: {payload}")

            handler = self._find_handler(topic)

            if handler:
                try:
                    processed = bool(handler(topic, payload))
                    if not processed:
                        failure_reason = "handler_returned_false"
                except (OperationalError, InterfaceError) as e:
                    retryable_failure = True
                    failure_reason = f"temporary_database_error: {type(e).__name__}"
                    logger.warning(
                        "MQTT 入站消息因数据库暂时不可用而保留未 ACK: "
                        "topic=%s error=%s",
                        topic,
                        e,
                    )
                except Exception as e:
                    failure_reason = f"handler_exception: {e}"
                    id_hint = self._extract_id_from_topic(topic, '') or ''
                    extra = f" [ID: {id_hint}]" if id_hint else ""
                    logger.error(f"处理器执行异常: {topic}{extra}, {e}", exc_info=True)
            else:
                failure_reason = "handler_not_found"
                id_hint = self._extract_id_from_topic(topic, '') or ''
                extra = f" [ID: {id_hint}]" if id_hint else ""
                logger.warning(f"未找到匹配的处理器: {topic}{extra}")

        except json.JSONDecodeError as e:
            failure_reason = f"json_decode_error: {e}"
            logger.error(f"JSON解析失败: {topic}, {e}")
            logger.debug(f"  原始消息: {msg.payload}")
        except Exception as e:
            failure_reason = f"message_exception: {e}"
            logger.error(f"消息处理异常: {topic}, {e}", exc_info=True)
        finally:
            close_old_connections()

        if processed:
            with self._lifecycle_lock:
                self._inbound_retry_delay = self.INBOUND_RETRY_MIN_DELAY
            self._ack_inbound(client, msg)
            return

        if retryable_failure:
            # Paho manual_ack=True：不调用 ack 即可让 persistent session 在
            # 重新连接后重投。主动调度重连，避免消息在当前在线连接内无限挂起；
            # 临时基础设施故障不能进入永久死信。
            self._schedule_retryable_reconnect(client)
            return

        # 永久格式错误先进入 Redis 死信流。只有死信持久化成功才 ACK；
        # Redis 也不可用时保持未 ACK，避免静默吞掉消息。
        self._dead_letter_or_leave_unacked(client, msg, failure_reason)

    @staticmethod
    def _dead_letter_or_leave_unacked(client, msg, reason: str) -> bool:
        """死信持久化成功后 ACK；Redis 失败则保留 broker 重投。"""
        try:
            from services.mqtt_command_bus import get_mqtt_command_bus

            get_mqtt_command_bus().dead_letter_inbound(
                topic=getattr(msg, "topic", ""),
                payload=bytes(getattr(msg, "payload", b"") or b""),
                reason=reason,
                qos=getattr(msg, "qos", 0),
                message_id=getattr(msg, "mid", 0),
            )
        except Exception as exc:
            logger.error(
                "入站消息处理失败且死信持久化失败，保留未 ACK topic=%s err=%s",
                getattr(msg, "topic", ""),
                exc,
                exc_info=True,
            )
            return False
        return MQTTService._ack_inbound(client, msg)

    @staticmethod
    def _ack_inbound(client, msg) -> bool:
        qos = int(getattr(msg, "qos", 0) or 0)
        if qos <= 0:
            return True
        try:
            rc = client.ack(getattr(msg, "mid", 0), qos)
            if rc != mqtt.MQTT_ERR_SUCCESS:
                logger.error(
                    "MQTT 手动 ACK 失败 topic=%s mid=%s qos=%s rc=%s",
                    getattr(msg, "topic", ""), getattr(msg, "mid", 0), qos, rc,
                )
                return False
            return True
        except Exception as exc:
            logger.error("MQTT 手动 ACK 异常: %s", exc, exc_info=True)
            return False

    def _find_handler(self, topic: str) -> Optional[Callable]:
        """查找匹配的消息处理器"""
        for pattern, handler in self.handlers.items():
            if self._topic_matches(topic, pattern):
                return handler
        return None

    @staticmethod
    def _topic_matches(topic: str, pattern: str) -> bool:
        """检查主题是否匹配模式（支持MQTT通配符）"""
        pattern_regex = pattern.replace('+', r'[^/]+')
        pattern_regex = pattern_regex.replace('#', r'.*')
        pattern_regex = f'^{pattern_regex}$'
        return re.match(pattern_regex, topic) is not None


# 全局单例
mqtt_service = MQTTService()
