"""
虚拟 SG90 舵机 —— 复刻 hardware/wemos-d1/devices/SG_90/sg90_control.ino

MQTT 主题：
  状态:  iot/devices/{device_id}/status
  控制:  iot/devices/{device_id}/control

支持命令：set_angle / current_status / set_status_interval
"""
import argparse
import logging
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common.mqtt_node import MqttNode
from common.schema import ParamSpec

log = logging.getLogger(__name__)


class SG90Servo(MqttNode):
    NODE_TYPE = "device"
    ID_FIELD = "device_id"
    LABEL = "SG90 舵机 (0-180°)"

    DEFAULT_STATUS_REPORT_INTERVAL = 120
    DEFAULT_INITIAL_ANGLE = 90

    PARAMS_SCHEMA = [
        ParamSpec("status_report_interval", "float", label="心跳间隔(秒)",
                  default=DEFAULT_STATUS_REPORT_INTERVAL, min=5, max=86400),
        ParamSpec("initial_angle", "float", label="初始角度(°)",
                  default=DEFAULT_INITIAL_ANGLE, min=0, max=180),
    ]

    SUPPORTED_COMMANDS = [
        {"command": "set_angle", "label": "设置角度",
         "args": [{"name": "angle", "type": "float", "min": 0, "max": 180}]},
        {"command": "current_status", "label": "查询状态"},
        {"command": "set_status_interval", "label": "设置心跳间隔",
         "args": [{"name": "interval", "type": "float", "min": 10, "max": 600}]},
    ]

    def __init__(
        self,
        node_id: str,
        broker: str,
        port: int = 1883,
        username: str = "",
        password: str = "",
        status_report_interval: float = DEFAULT_STATUS_REPORT_INTERVAL,
        initial_angle: float = DEFAULT_INITIAL_ANGLE,
    ):
        super().__init__(
            node_id=node_id,
            broker=broker,
            port=port,
            username=username,
            password=password,
            status_report_interval=status_report_interval,
        )
        angle = self.coerce_number(initial_angle)
        if angle is None or not 0 <= angle <= 180:
            raise ValueError(f"initial_angle 应在 0-180° 之间，收到 {initial_angle!r}")
        self.current_angle = angle

    # ============ status payload ============
    def build_status_payload(self) -> dict:
        return {
            "angle": self.current_angle,
            "statusReportInterval": self.status_report_interval,
        }

    # ============ 控制命令 ============
    def handle_command(self, command: str, payload: dict, check_code: Optional[str]) -> None:
        if command == "set_angle":
            angle = self.coerce_number(payload.get("angle"))
            if angle is None:
                log.warning(f"[{self.node_id}] ✗ angle 字段无效: {payload.get('angle')!r}")
                return
            if 0 <= angle <= 180:
                self.current_angle = angle
                log.info(f"[{self.node_id}] ✓ angle → {angle}°")
                self.publish_status("angle_updated", check_code)
            else:
                log.warning(f"[{self.node_id}] ✗ angle 越界（0-180）: {angle}")

        elif command == "current_status":
            log.info(f"[{self.node_id}] ✓ 响应 current_status 查询")
            self.publish_status("check_current_angle", check_code)

        elif command == "set_status_interval":
            interval = self.coerce_number(payload.get("interval"))
            if interval is None:
                log.warning(f"[{self.node_id}] ✗ interval 字段无效: {payload.get('interval')!r}")
                return
            if 10 <= interval <= 600:
                self.status_report_interval = interval
                log.info(f"[{self.node_id}] ✓ statusReportInterval → {interval}s")
                self.publish_status("status_interval_updated", check_code)
            else:
                log.warning(f"[{self.node_id}] ✗ interval 越界（10-600）: {interval}")

        else:
            log.warning(f"[{self.node_id}] ⚠ 未知命令: {command}")


def main():
    parser = argparse.ArgumentParser(description="虚拟 SG90 舵机")
    parser.add_argument("--id", default="sg90_001", help="device_id")
    parser.add_argument("--broker", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--status-report-interval", type=float, default=SG90Servo.DEFAULT_STATUS_REPORT_INTERVAL)
    parser.add_argument("--initial-angle", type=float, default=SG90Servo.DEFAULT_INITIAL_ANGLE)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    node = SG90Servo(
        node_id=args.id,
        broker=args.broker,
        port=args.port,
        username=args.username,
        password=args.password,
        status_report_interval=args.status_report_interval,
        initial_angle=args.initial_angle,
    )
    try:
        node.run()
    except KeyboardInterrupt:
        log.info("⚠ Ctrl-C，停止中…")
        node.stop()


if __name__ == "__main__":
    main()
