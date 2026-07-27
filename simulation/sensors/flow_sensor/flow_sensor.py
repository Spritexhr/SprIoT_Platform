"""
虚拟流量传感器 —— 新增节点，无对应 .ino 固件

测量瞬时流量 (L/min) 与累计体积 (L)。累计体积由 flow_rate × elapsed_time 增量积分得到，
通过 reset_volume 命令清零（适合按批次/班次统计）。

数据字段：flow_rate (L/min)、accumulated_volume (L)
支持命令：
  enable / disable
  set_interval / set_data_interval
  set_status_interval
  reset_volume                  —— 清零累计体积
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
from common.waveforms import build_waveform_map

log = logging.getLogger(__name__)


DEFAULT_WAVEFORMS = {
    "flow_rate": {
        "type": "sine",
        "min": 8.0,
        "max": 25.0,
        "period": 600,
        "jitter": 0.8,
    },
}


class FlowSensor(MqttNode):
    NODE_TYPE = "sensor"
    ID_FIELD = "sensor_id"
    LABEL = "流量传感器 (L/min)"

    DEFAULT_SAMPLING_INTERVAL = 10
    DEFAULT_STATUS_REPORT_INTERVAL = 60

    FLOW_PRECISION = 2
    VOLUME_PRECISION = 3

    PARAMS_SCHEMA = [
        ParamSpec("sampling_interval", "float", label="采样间隔(秒)",
                  default=DEFAULT_SAMPLING_INTERVAL, min=1, max=86400),
        ParamSpec("status_report_interval", "float", label="心跳间隔(秒)",
                  default=DEFAULT_STATUS_REPORT_INTERVAL, min=5, max=86400),
        ParamSpec("waveforms", "waveform_map", label="数据波形",
                  fields=["flow_rate"], default=DEFAULT_WAVEFORMS,
                  help="瞬时流量波形；累计体积由积分自动得出"),
    ]

    SUPPORTED_COMMANDS = [
        {"command": "enable", "label": "启用"},
        {"command": "disable", "label": "禁用"},
        {"command": "set_interval", "label": "设置采样间隔",
         "args": [{"name": "interval", "type": "float", "min": 5, "max": 3600}]},
        {"command": "set_status_interval", "label": "设置心跳间隔",
         "args": [{"name": "interval", "type": "float", "min": 30, "max": 600}]},
        {"command": "reset_volume", "label": "累计体积清零"},
    ]

    def __init__(
        self,
        node_id: str,
        broker: str,
        port: int = 1883,
        username: str = "",
        password: str = "",
        sampling_interval: float = DEFAULT_SAMPLING_INTERVAL,
        status_report_interval: float = DEFAULT_STATUS_REPORT_INTERVAL,
        waveforms: Optional[dict] = None,
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
        self.sampling_interval = sampling_interval
        self.is_enabled = True
        self._last_sample_time = 0.0

        # 累计体积（L）
        self.accumulated_volume = 0.0
        self._last_integrate_time = time.time()

        wf_cfg = {**DEFAULT_WAVEFORMS, **(waveforms or {})}
        self._waveforms = build_waveform_map(wf_cfg)
        self._current_flow_rate = 0.0  # 心跳时可用

    def build_status_payload(self) -> dict:
        return {
            "is_enabled": self.is_enabled,
            "samplingInterval": self.sampling_interval,
            "statusReportInterval": self.status_report_interval,
            "accumulated_volume": round(self.accumulated_volume, self.VOLUME_PRECISION),
            "current_flow_rate": round(self._current_flow_rate, self.FLOW_PRECISION),
        }

    def _read_flow_rate(self) -> float:
        rate = max(0.0, self._waveforms["flow_rate"].sample())
        self._current_flow_rate = rate
        return rate

    def _integrate_volume(self, flow_rate_lpm: float) -> None:
        """flow_rate L/min × elapsed minutes = added volume L"""
        now = time.time()
        dt_min = (now - self._last_integrate_time) / 60.0
        self._last_integrate_time = now
        if dt_min > 0:
            self.accumulated_volume += flow_rate_lpm * dt_min

    def handle_command(self, command: str, payload: dict, check_code: Optional[str]) -> None:
        if command in ("set_interval", "set_data_interval"):
            interval = self.coerce_number(payload.get("interval"), 0.0)
            if 5 <= interval <= 3600:
                self.sampling_interval = interval
                log.info(f"[{self.node_id}] ✓ samplingInterval → {interval}s")
                self.publish_status("interval_updated", check_code)
            else:
                log.warning(f"[{self.node_id}] ✗ interval 越界（5-3600）: {interval}")

        elif command == "set_status_interval":
            interval = self.coerce_number(payload.get("interval"), 0.0)
            if 30 <= interval <= 600:
                self.status_report_interval = interval
                log.info(f"[{self.node_id}] ✓ statusReportInterval → {interval}s")
                self.publish_status("status_interval_updated", check_code)
            else:
                log.warning(f"[{self.node_id}] ✗ interval 越界（30-600）: {interval}")

        elif command == "enable":
            self.is_enabled = True
            self._last_integrate_time = time.time()  # 重新计时，避免暂停期被算入
            self.publish_status("sensor_enabled", check_code)

        elif command == "disable":
            self.is_enabled = False
            self.publish_status("sensor_disabled", check_code)

        elif command == "reset_volume":
            self.accumulated_volume = 0.0
            log.info(f"[{self.node_id}] ✓ 累计体积已清零")
            self.publish_status("volume_reset", check_code)

        else:
            log.warning(f"[{self.node_id}] ⚠ 未知命令: {command}")

    def on_tick(self) -> None:
        if not self.is_enabled:
            return
        now = time.time()
        if now - self._last_sample_time < self.sampling_interval:
            return

        flow_rate = self._read_flow_rate()
        self._integrate_volume(flow_rate)

        data_msg = {
            "sensor_id": self.node_id,
            "data": {
                "flow_rate": round(flow_rate, self.FLOW_PRECISION),
                "accumulated_volume": round(self.accumulated_volume, self.VOLUME_PRECISION),
            },
            "timestamp": self.now_ts(),
        }
        if self.publish_json(self.topic_data, data_msg):
            log.info(
                f"[{self.node_id}] → data flow_rate={flow_rate:.2f}L/min "
                f"accumulated={self.accumulated_volume:.3f}L"
            )
        self._last_sample_time = now


def main():
    parser = argparse.ArgumentParser(description="虚拟流量传感器")
    parser.add_argument("--id", default="FLOW-001")
    parser.add_argument("--broker", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--sampling-interval", type=float,
                        default=FlowSensor.DEFAULT_SAMPLING_INTERVAL)
    parser.add_argument("--status-report-interval", type=float,
                        default=FlowSensor.DEFAULT_STATUS_REPORT_INTERVAL)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    node = FlowSensor(
        node_id=args.id,
        broker=args.broker,
        port=args.port,
        username=args.username,
        password=args.password,
        sampling_interval=args.sampling_interval,
        status_report_interval=args.status_report_interval,
    )
    try:
        node.run()
    except KeyboardInterrupt:
        node.stop()


if __name__ == "__main__":
    main()
