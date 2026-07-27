"""
虚拟触摸开关 —— 复刻
hardware/wemos-d1/sensors/touch_sensor_switch/touch_sensor_switch_v1.ino

事件驱动：只有 switch 状态变化时才上报 data；status 心跳定期上报，且 payload 内含 switch
支持命令：enable / disable / set_status_interval

注意：与周期上报的传感器不同，本节点无 sampling_interval / set_data_interval
仿真侧用 flip_period_s 决定多久翻转一次状态（默认 15s）
"""
import argparse
import logging
import os
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common.mqtt_node import MqttNode
from common.schema import ParamSpec

log = logging.getLogger(__name__)


class TouchSensorSwitch(MqttNode):
    NODE_TYPE = "sensor"
    ID_FIELD = "sensor_id"
    LABEL = "触摸开关（事件驱动）"

    DEFAULT_STATUS_REPORT_INTERVAL = 120
    DEFAULT_FLIP_PERIOD_S = 15.0
    DEFAULT_INITIAL_STATE = False

    PARAMS_SCHEMA = [
        ParamSpec("status_report_interval", "float", label="心跳间隔(秒)",
                  default=DEFAULT_STATUS_REPORT_INTERVAL, min=5, max=86400),
        ParamSpec("flip_period_s", "float", label="翻转周期(秒)",
                  default=DEFAULT_FLIP_PERIOD_S, min=0.5,
                  help="仿真侧多久自动翻转一次开关状态"),
        ParamSpec("initial_state", "bool", label="初始状态",
                  default=DEFAULT_INITIAL_STATE),
    ]

    SUPPORTED_COMMANDS = [
        {"command": "enable", "label": "启用"},
        {"command": "disable", "label": "禁用"},
        {"command": "set_status_interval", "label": "设置心跳间隔",
         "args": [{"name": "interval", "type": "float", "min": 30, "max": 600}]},
    ]

    def __init__(
        self,
        node_id: str,
        broker: str,
        port: int = 1883,
        username: str = "",
        password: str = "",
        status_report_interval: float = DEFAULT_STATUS_REPORT_INTERVAL,
        flip_period_s: float = DEFAULT_FLIP_PERIOD_S,
        initial_state: bool = DEFAULT_INITIAL_STATE,
    ):
        super().__init__(
            node_id=node_id,
            broker=broker,
            port=port,
            username=username,
            password=password,
            status_report_interval=status_report_interval,
        )
        self.topic_data = f"iot/sensors/{node_id}/data"
        self.is_enabled = True

        self.flip_period_s = float(flip_period_s)
        self.switch_state = bool(initial_state)
        self._last_flip_time = 0.0  # 0 表示从未翻转过；启动后立即可触发首次翻转

    def build_status_payload(self) -> dict:
        return {
            "is_enabled": self.is_enabled,
            "statusReportInterval": self.status_report_interval,
            "switch": self.switch_state,
        }

    def handle_command(self, command: str, payload: dict, check_code: Optional[str]) -> None:
        if command == "set_status_interval":
            interval = self.coerce_number(payload.get("interval"), 0.0)
            if 30 <= interval <= 600:
                self.status_report_interval = interval
                log.info(f"[{self.node_id}] ✓ statusReportInterval → {interval}s")
                self.publish_status("status_interval_updated", check_code)
            else:
                log.warning(f"[{self.node_id}] ✗ interval 越界（30-600）: {interval}")

        elif command == "enable":
            self.is_enabled = True
            self.publish_status("sensor_enabled", check_code)

        elif command == "disable":
            self.is_enabled = False
            self.publish_status("sensor_disabled", check_code)

        else:
            log.warning(f"[{self.node_id}] ⚠ 未知命令: {command}")

    def _publish_data(self) -> None:
        data_msg = {
            "sensor_id": self.node_id,
            "data": {"switch": self.switch_state},
            "timestamp": self.now_ts(),
        }
        if self.publish_json(self.topic_data, data_msg):
            log.info(f"[{self.node_id}] → data switch={self.switch_state}")

    def on_tick(self) -> None:
        if not self.is_enabled:
            return
        now = time.time()
        if self._last_flip_time == 0.0:
            self._last_flip_time = now
            return
        if now - self._last_flip_time < self.flip_period_s:
            return

        # 翻转 + 发数据（事件驱动，仅状态变化时发）
        self.switch_state = not self.switch_state
        self._last_flip_time = now
        self._publish_data()


def main():
    parser = argparse.ArgumentParser(description="虚拟触摸开关")
    parser.add_argument("--id", default="Switch-Touch-001")
    parser.add_argument("--broker", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--status-report-interval", type=float,
                        default=TouchSensorSwitch.DEFAULT_STATUS_REPORT_INTERVAL)
    parser.add_argument("--flip-period-s", type=float,
                        default=TouchSensorSwitch.DEFAULT_FLIP_PERIOD_S)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    node = TouchSensorSwitch(
        node_id=args.id,
        broker=args.broker,
        port=args.port,
        username=args.username,
        password=args.password,
        status_report_interval=args.status_report_interval,
        flip_period_s=args.flip_period_s,
    )
    try:
        node.run()
    except KeyboardInterrupt:
        node.stop()


if __name__ == "__main__":
    main()
