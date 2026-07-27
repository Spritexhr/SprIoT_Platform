"""
webui 自有 MQTT 客户端 —— 节点级实时监控与控制

- 订阅 iot/sensors/+/status、iot/devices/+/status、iot/sensors/+/data，
  维护内存里的节点实时状态表（不入库）
- 通过发布 iot/{type}s/{id}/control 实现节点级 enable/disable/set_interval 等命令，
  与 Django/硬件走完全相同的协议路径
- 注意：这类控制是"运行态"的，子进程重启后会恢复 DB 配置

在线判定：last_seen 距今 < statusReportInterval * 2 + 10s（status payload 自带该值）。
client_id 用 sim-webui-<pid>，避免与节点的 WemosD1-<id> 命名空间冲突。
"""
import json
import logging
import math
import os
import threading
import time
from typing import Callable, Dict, Optional

import paho.mqtt.client as mqtt

from common.mqtt_compat import create_client, reason_code_value

log = logging.getLogger(__name__)

ONLINE_MARGIN_S = 10
DEFAULT_STATUS_INTERVAL = 120


class MqttMonitor:
    def __init__(self, on_event: Optional[Callable[[dict], None]] = None):
        self._client: Optional[mqtt.Client] = None
        self._broker_cfg: Optional[dict] = None
        self._lock = threading.Lock()
        self._on_event = on_event
        self.connected = False
        # "{sensor|device}:{node_id}" -> 状态字典
        self.nodes: Dict[str, dict] = {}

    # ============ 连接管理 ============

    def configure(self, broker: dict):
        """连接（或切换）broker。broker: {host, port, username, password}"""
        with self._lock:
            same = (
                self._broker_cfg
                and self._broker_cfg["host"] == broker["host"]
                and self._broker_cfg.get("port", 1883) == broker.get("port", 1883)
                and self._broker_cfg.get("username", "") == broker.get("username", "")
            )
            if same and self.connected:
                return
            self._disconnect_locked()
            self._broker_cfg = broker

            client = create_client(client_id=f"sim-webui-{os.getpid()}")
            if broker.get("username"):
                client.username_pw_set(broker["username"], broker.get("password", ""))
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.reconnect_delay_set(min_delay=1, max_delay=60)
            try:
                client.connect_async(broker["host"], broker.get("port", 1883), keepalive=60)
                client.loop_start()
                self._client = client
            except Exception as e:
                log.error(f"monitor 连接 broker 失败: {e}")

    def _disconnect_locked(self):
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self.connected = False

    def stop(self):
        with self._lock:
            self._disconnect_locked()

    # ============ MQTT 回调（paho 线程） ============

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        rc = reason_code_value(reason_code)
        if rc == 0:
            self.connected = True
            client.subscribe([
                ("iot/sensors/+/status", 0),
                ("iot/devices/+/status", 0),
                ("iot/sensors/+/data", 0),
            ])
            log.info(f"monitor ✓ 已连接 {self._broker_cfg['host']} 并订阅 status/data")
            self._emit({"type": "monitor_state", "connected": True})
        else:
            log.error(f"monitor ✗ 连接失败 rc={rc}")

    def _on_disconnect(
        self,
        client,
        userdata,
        disconnect_flags_or_rc,
        reason_code=None,
        properties=None,
    ):
        rc = reason_code_value(
            disconnect_flags_or_rc if reason_code is None else reason_code
        )
        self.connected = False
        self._emit({"type": "monitor_state", "connected": False})
        if rc != 0:
            log.warning("monitor ⚠ MQTT 断开，自动重连中…")

    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        if len(parts) != 4:
            return
        _, plural, node_id, kind = parts
        node_type = plural[:-1]  # sensors -> sensor
        key = f"{node_type}:{node_id}"
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        now = time.time()
        entry = self.nodes.setdefault(key, {
            "node_type": node_type, "node_id": node_id,
            "last_status": None, "last_data": None,
            "last_event": None, "last_seen": 0.0,
            "status_interval": DEFAULT_STATUS_INTERVAL,
        })
        entry["last_seen"] = now

        if kind == "status":
            status = payload.get("status") or {}
            entry["last_status"] = status
            entry["last_event"] = payload.get("event")
            interval = status.get("statusReportInterval")
            if (
                isinstance(interval, (int, float))
                and not isinstance(interval, bool)
                and math.isfinite(interval)
                and interval > 0
            ):
                entry["status_interval"] = interval
            self._emit({"type": "node_status", "node": self._node_out(key)})
        elif kind == "data":
            entry["last_data"] = payload.get("data")
            self._emit({"type": "node_data", "node": self._node_out(key)})

    def _emit(self, event: dict):
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:
                log.exception("monitor 事件回调失败")

    # ============ 查询 / 控制 ============

    def _node_out(self, key: str) -> dict:
        e = self.nodes[key]
        now = time.time()
        online = (
            e["last_seen"] > 0
            and now - e["last_seen"] < e["status_interval"] * 2 + ONLINE_MARGIN_S
        )
        return {
            "node_type": e["node_type"],
            "node_id": e["node_id"],
            "online": online,
            "last_seen": e["last_seen"],
            "age_s": round(now - e["last_seen"], 1) if e["last_seen"] else None,
            "last_event": e["last_event"],
            "last_status": e["last_status"],
            "last_data": e["last_data"],
        }

    def snapshot(self) -> list:
        return [self._node_out(k) for k in sorted(self.nodes.keys())]

    def publish_command(self, node_type: str, node_id: str, command: str,
                        args: Optional[dict] = None) -> bool:
        """向节点 control 主题发布命令。check_code 标记 webui 来源，便于在 status 流中关联回包"""
        if node_type not in ("sensor", "device"):
            raise ValueError(f"node_type 应为 sensor/device，收到 {node_type!r}")
        if not self.connected or not self._client:
            raise RuntimeError("monitor 未连接 broker，无法发送命令")
        payload = {"command": command, "check_code": f"webui-{int(time.time())}"}
        if args:
            payload.update(args)
        topic = f"iot/{node_type}s/{node_id}/control"
        result = self._client.publish(topic, json.dumps(payload))
        ok = result.rc == mqtt.MQTT_ERR_SUCCESS
        log.info(f"monitor → {topic} {payload} {'✓' if ok else '✗'}")
        return ok
