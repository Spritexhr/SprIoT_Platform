"""
虚拟节点基类 —— 复刻 hardware/wemos-d1/*.ino 中通用的 WiFi+MQTT+NTP 行为

负责：
- MQTT 连接 / 自动重连（指数退避，由 paho-mqtt 内置）
- 订阅 control 主题
- 上线 / 心跳 status 上报
- check_code 原样回传
- 主循环 tick（子类用 on_tick() 钩子做数据采集）

子类需要实现：
- NODE_TYPE      "sensor" 或 "device"
- ID_FIELD       JSON payload 中的 ID 字段名："sensor_id" 或 "device_id"
- build_status_payload() —— 返回 status 字典
- handle_command()       —— 处理收到的控制命令
- on_tick()              —— 可选；传感器在这里发数据
"""
import json
import logging
import math
import threading
import time
from typing import Optional

import paho.mqtt.client as mqtt

from common.mqtt_compat import create_client, reason_code_value

log = logging.getLogger(__name__)


class MqttNode:
    NODE_TYPE: str = ""      # 子类填: "sensor" / "device"
    ID_FIELD: str = ""       # 子类填: "sensor_id" / "device_id"

    RECONNECT_MIN_DELAY = 1
    RECONNECT_MAX_DELAY = 120

    def __init__(
        self,
        node_id: str,
        broker: str,
        port: int = 1883,
        username: str = "",
        password: str = "",
        status_report_interval: float = 120,
    ):
        if not self.NODE_TYPE or not self.ID_FIELD:
            raise NotImplementedError("子类必须设置 NODE_TYPE 和 ID_FIELD")

        self.node_id = node_id
        self.broker = broker
        self.port = port
        interval = self.coerce_number(status_report_interval)
        if interval is None or interval <= 0:
            raise ValueError(f"status_report_interval 必须是大于 0 的有限数字，收到 {status_report_interval!r}")
        self.status_report_interval = interval

        # 主题模板与 .ino 中一致
        self.topic_control = f"iot/{self.NODE_TYPE}s/{node_id}/control"
        self.topic_status = f"iot/{self.NODE_TYPE}s/{node_id}/status"

        # client_id 模仿固件: WemosD1-<id>
        self.client = create_client(client_id=f"WemosD1-{node_id}")
        if username:
            self.client.username_pw_set(username, password)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(
            min_delay=self.RECONNECT_MIN_DELAY,
            max_delay=self.RECONNECT_MAX_DELAY,
        )

        self._running = False
        self._last_status_time = 0.0
        self._stop_event = threading.Event()

    # ============ 时间戳（替代 NTP）============
    @staticmethod
    def now_ts() -> int:
        return int(time.time())

    @staticmethod
    def coerce_number(value, default=None) -> Optional[float]:
        """把 MQTT/配置里的连续数值安全转换为有限浮点数，拒绝 bool、NaN 与无穷大。"""
        if isinstance(value, bool):
            return default
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    # ============ 抽象接口 ============
    def build_status_payload(self) -> dict:
        raise NotImplementedError

    def handle_command(self, command: str, payload: dict, check_code: Optional[str]) -> None:
        raise NotImplementedError

    def on_tick(self) -> None:
        """子类可选实现：每个主循环 tick 调用一次，用于发数据等周期任务"""
        pass

    # ============ MQTT 回调 ============
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        rc = reason_code_value(reason_code)
        if rc == 0:
            log.info(f"[{self.node_id}] ✓ MQTT 已连接 {self.broker}:{self.port}")
            client.subscribe(self.topic_control)
            log.info(f"[{self.node_id}] ✓ 已订阅 {self.topic_control}")
            self.publish_status("online")
            self._last_status_time = time.time()
        else:
            log.error(f"[{self.node_id}] ✗ MQTT 连接失败 rc={rc}")

    def _on_disconnect(
        self,
        client,
        userdata,
        disconnect_flags_or_rc,
        reason_code=None,
        properties=None,
    ):
        # Paho 1.x: 第 3 个参数就是 rc；2.x: 第 4 个参数是 ReasonCode。
        rc = reason_code_value(
            disconnect_flags_or_rc if reason_code is None else reason_code
        )
        if rc != 0:
            log.warning(f"[{self.node_id}] ⚠ MQTT 意外断开 rc={rc}，自动重连中…")

    def _on_message(self, client, userdata, msg):
        log.info(f"[{self.node_id}] ← {msg.topic}: {msg.payload!r}")
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            log.error(f"[{self.node_id}] ✗ JSON 解析失败: {e}")
            return

        command = payload.get("command")
        if not command:
            log.error(f"[{self.node_id}] ✗ 消息缺少 command 字段")
            return

        check_code = payload.get("check_code")
        check_code = str(check_code).strip() if check_code else None

        try:
            self.handle_command(command, payload, check_code)
        except Exception as e:
            log.exception(f"[{self.node_id}] ✗ 命令处理异常: {e}")

    # ============ 发布 ============
    def publish_status(self, event: str, check_code: Optional[str] = None) -> bool:
        msg = {
            self.ID_FIELD: self.node_id,
            "event": event,
            "status": self.build_status_payload(),
            "timestamp": self.now_ts(),
        }
        if check_code:
            msg["check_code"] = check_code

        result = self.client.publish(self.topic_status, json.dumps(msg))
        ok = result.rc == mqtt.MQTT_ERR_SUCCESS
        if ok:
            tail = f" check_code={check_code}" if check_code else ""
            log.info(f"[{self.node_id}] → status event={event}{tail}")
        else:
            log.error(f"[{self.node_id}] ✗ status 发布失败 rc={result.rc}")
        return ok

    def publish_json(self, topic: str, payload: dict) -> bool:
        result = self.client.publish(topic, json.dumps(payload))
        ok = result.rc == mqtt.MQTT_ERR_SUCCESS
        if not ok:
            log.error(f"[{self.node_id}] ✗ 发布到 {topic} 失败 rc={result.rc}")
        return ok

    # ============ 主循环 ============
    def run(self) -> None:
        try:
            self.client.connect(self.broker, self.port, keepalive=60)
        except OSError as e:
            log.error(f"[{self.node_id}] ✗ 无法连接 {self.broker}:{self.port}: {e}")
            return

        self.client.loop_start()
        self._running = True
        self._last_status_time = time.time()
        log.info(f"[{self.node_id}] 启动完成，进入主循环")

        try:
            while self._running and not self._stop_event.is_set():
                now = time.time()
                if now - self._last_status_time >= self.status_report_interval:
                    self.publish_status("heartbeat")
                    self._last_status_time = now
                self.on_tick()
                self._stop_event.wait(0.1)
        finally:
            self.stop()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass
        log.info(f"[{self.node_id}] 已停止")
