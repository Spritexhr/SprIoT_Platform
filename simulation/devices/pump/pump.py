"""
虚拟水泵/动力泵设备 —— 新增节点，无对应 .ino 固件

执行器（device），不上报数据，仅状态心跳 + 命令响应。
功率单位 kW，可由命令调整目标功率；启停时实际功率会向目标渐变（模拟惯性）。

支持命令：
  start / stop                —— 启停（stop 时 power_kw → 0）
  set_power {val}             —— 设置目标功率 (kW)，范围由 max_power_kw 决定
  current_status              —— 上报当前状态
  set_status_interval {val}   —— 调整心跳间隔
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


class Pump(MqttNode):
    NODE_TYPE = "device"
    ID_FIELD = "device_id"
    LABEL = "水泵 (kW，带启停渐变)"

    DEFAULT_STATUS_REPORT_INTERVAL = 60
    DEFAULT_MAX_POWER_KW = 15.0      # 中型水泵
    DEFAULT_RAMP_RATE_KW_PER_S = 5.0 # 每秒变化最大 5 kW，模拟启停渐变

    PARAMS_SCHEMA = [
        ParamSpec("status_report_interval", "float", label="心跳间隔(秒)",
                  default=DEFAULT_STATUS_REPORT_INTERVAL, min=5, max=86400),
        ParamSpec("max_power_kw", "float", label="额定功率(kW)",
                  default=DEFAULT_MAX_POWER_KW, min=0.1),
        ParamSpec("ramp_rate_kw_per_s", "float", label="功率渐变速率(kW/s)",
                  default=DEFAULT_RAMP_RATE_KW_PER_S, min=0.01,
                  help="实际功率向目标功率渐变的最大速率，模拟电机惯性"),
        ParamSpec("initial_power_kw", "float", label="初始功率(kW)",
                  default=0.0, min=0),
    ]

    SUPPORTED_COMMANDS = [
        {"command": "start", "label": "启动"},
        {"command": "stop", "label": "停机"},
        {"command": "set_power", "label": "设置功率",
         "args": [{"name": "val", "type": "float", "min": 0}]},
        {"command": "current_status", "label": "查询状态"},
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
        max_power_kw: float = DEFAULT_MAX_POWER_KW,
        ramp_rate_kw_per_s: float = DEFAULT_RAMP_RATE_KW_PER_S,
        initial_power_kw: float = 0.0,
    ):
        super().__init__(
            node_id=node_id,
            broker=broker,
            port=port,
            username=username,
            password=password,
            status_report_interval=status_report_interval,
        )
        self.max_power_kw = float(max_power_kw)
        self.ramp_rate = float(ramp_rate_kw_per_s)

        self.is_running = initial_power_kw > 0
        self.target_power_kw = float(initial_power_kw)
        self.power_kw = float(initial_power_kw)
        self._last_ramp_time = time.time()

    def build_status_payload(self) -> dict:
        return {
            "is_running": self.is_running,
            "power_kw": round(self.power_kw, 3),
            "target_power_kw": round(self.target_power_kw, 3),
            "max_power_kw": self.max_power_kw,
            "statusReportInterval": self.status_report_interval,
        }

    def _clamp_target(self, value: float) -> float:
        return max(0.0, min(self.max_power_kw, float(value)))

    def handle_command(self, command: str, payload: dict, check_code: Optional[str]) -> None:
        if command == "start":
            # 启动：如果之前 target=0 默认拉到 max 的 50%，否则按已有 target 跑
            self.is_running = True
            if self.target_power_kw <= 0:
                self.target_power_kw = self.max_power_kw * 0.5
            log.info(f"[{self.node_id}] ✓ 启动，target={self.target_power_kw} kW")
            self.publish_status("pump_started", check_code)

        elif command == "stop":
            self.is_running = False
            self.target_power_kw = 0.0
            log.info(f"[{self.node_id}] ✓ 停机")
            self.publish_status("pump_stopped", check_code)

        elif command == "set_power":
            try:
                val = float(payload.get("val", payload.get("power", 0)))
            except (TypeError, ValueError):
                log.warning(f"[{self.node_id}] ✗ set_power 参数非法: {payload}")
                return
            self.target_power_kw = self._clamp_target(val)
            self.is_running = self.target_power_kw > 0
            log.info(f"[{self.node_id}] ✓ target_power → {self.target_power_kw} kW")
            self.publish_status("power_updated", check_code)

        elif command == "current_status":
            self.publish_status("check_current_status", check_code)

        elif command == "set_status_interval":
            interval = self.coerce_number(payload.get("interval"), 0.0)
            if 30 <= interval <= 600:
                self.status_report_interval = interval
                log.info(f"[{self.node_id}] ✓ statusReportInterval → {interval}s")
                self.publish_status("status_interval_updated", check_code)
            else:
                log.warning(f"[{self.node_id}] ✗ interval 越界（30-600）: {interval}")

        else:
            log.warning(f"[{self.node_id}] ⚠ 未知命令: {command}")

    def on_tick(self) -> None:
        # 实际功率向 target 渐变，模拟电机加减速
        now = time.time()
        dt = now - self._last_ramp_time
        self._last_ramp_time = now
        if dt <= 0:
            return
        max_step = self.ramp_rate * dt
        diff = self.target_power_kw - self.power_kw
        if abs(diff) <= max_step:
            self.power_kw = self.target_power_kw
        else:
            self.power_kw += max_step if diff > 0 else -max_step


def main():
    parser = argparse.ArgumentParser(description="虚拟水泵设备（kW 输出功率）")
    parser.add_argument("--id", default="pump_001")
    parser.add_argument("--broker", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--status-report-interval", type=float,
                        default=Pump.DEFAULT_STATUS_REPORT_INTERVAL)
    parser.add_argument("--max-power-kw", type=float, default=Pump.DEFAULT_MAX_POWER_KW)
    parser.add_argument("--ramp-rate-kw-per-s", type=float, default=Pump.DEFAULT_RAMP_RATE_KW_PER_S)
    parser.add_argument("--initial-power-kw", type=float, default=0.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    node = Pump(
        node_id=args.id,
        broker=args.broker,
        port=args.port,
        username=args.username,
        password=args.password,
        status_report_interval=args.status_report_interval,
        max_power_kw=args.max_power_kw,
        ramp_rate_kw_per_s=args.ramp_rate_kw_per_s,
        initial_power_kw=args.initial_power_kw,
    )
    try:
        node.run()
    except KeyboardInterrupt:
        node.stop()


if __name__ == "__main__":
    main()
