"""
虚拟旋转编码器 —— 复刻
hardware/wemos-d1/sensors/rotation_sensor/rotation_sensor_v1.ino

数据字段：raw (0-1023 ADC 原值)、position (0-100 百分比)、angle (0-360 度)
三个值由 raw 派生：position = raw/1023*100, angle = raw/1023*360
status payload 中也带最新一次读数（与 .ino 行为一致）
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
    "raw": {
        "type": "sine",
        "min": 0.0,
        "max": 1023.0,
        "period": 60,
        "jitter": 5.0,
    },
}


class RotationSensor(MqttNode):
    NODE_TYPE = "sensor"
    ID_FIELD = "sensor_id"
    LABEL = "旋转编码器 (0-1023)"

    DEFAULT_SAMPLING_INTERVAL = 1
    DEFAULT_STATUS_REPORT_INTERVAL = 60

    RAW_MAX = 1023

    PARAMS_SCHEMA = [
        ParamSpec("sampling_interval", "float", label="采样间隔(秒)",
                  default=DEFAULT_SAMPLING_INTERVAL, min=1, max=86400),
        ParamSpec("status_report_interval", "float", label="心跳间隔(秒)",
                  default=DEFAULT_STATUS_REPORT_INTERVAL, min=5, max=86400),
        ParamSpec("waveforms", "waveform_map", label="数据波形",
                  fields=["raw"], default=DEFAULT_WAVEFORMS,
                  help="只配置 raw (0-1023)，position/angle 由 raw 派生"),
    ]

    SUPPORTED_COMMANDS = [
        {"command": "enable", "label": "启用"},
        {"command": "disable", "label": "禁用"},
        {"command": "set_interval", "label": "设置采样间隔",
         "args": [{"name": "interval", "type": "float", "min": 1, "max": 3600}]},
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

        # 最近一次读数（status 上报时一并带出）
        self._last_raw = 0
        self._last_position = 0
        self._last_angle = 0

        wf_cfg = {**DEFAULT_WAVEFORMS, **(waveforms or {})}
        self._waveforms = build_waveform_map(wf_cfg)

    def _sample(self) -> tuple:
        raw = int(max(0, min(self.RAW_MAX, self._waveforms["raw"].sample())))
        position = int(round(raw / self.RAW_MAX * 100))
        angle = int(round(raw / self.RAW_MAX * 360))
        self._last_raw = raw
        self._last_position = position
        self._last_angle = angle
        return raw, position, angle

    def build_status_payload(self) -> dict:
        return {
            "is_enabled": self.is_enabled,
            "samplingInterval": self.sampling_interval,
            "statusReportInterval": self.status_report_interval,
            "raw": self._last_raw,
            "position": self._last_position,
            "angle": self._last_angle,
        }

    def handle_command(self, command: str, payload: dict, check_code: Optional[str]) -> None:
        if command in ("set_interval", "set_data_interval"):
            interval = self.coerce_number(payload.get("interval"), 0.0)
            if 1 <= interval <= 3600:
                self.sampling_interval = interval
                log.info(f"[{self.node_id}] ✓ samplingInterval → {interval}s")
                self.publish_status("interval_updated", check_code)
            else:
                log.warning(f"[{self.node_id}] ✗ interval 越界（1-3600）: {interval}")

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
            self.publish_status("sensor_enabled", check_code)

        elif command == "disable":
            self.is_enabled = False
            self.publish_status("sensor_disabled", check_code)

        else:
            log.warning(f"[{self.node_id}] ⚠ 未知命令: {command}")

    def on_tick(self) -> None:
        if not self.is_enabled:
            return
        now = time.time()
        if now - self._last_sample_time < self.sampling_interval:
            return

        raw, position, angle = self._sample()
        data_msg = {
            "sensor_id": self.node_id,
            "data": {"raw": raw, "position": position, "angle": angle},
            "timestamp": self.now_ts(),
        }
        if self.publish_json(self.topic_data, data_msg):
            log.info(f"[{self.node_id}] → data raw={raw} pos={position}% angle={angle}°")
        self._last_sample_time = now


def main():
    parser = argparse.ArgumentParser(description="虚拟旋转编码器")
    parser.add_argument("--id", default="Rotation-001")
    parser.add_argument("--broker", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--sampling-interval", type=float,
                        default=RotationSensor.DEFAULT_SAMPLING_INTERVAL)
    parser.add_argument("--status-report-interval", type=float,
                        default=RotationSensor.DEFAULT_STATUS_REPORT_INTERVAL)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    node = RotationSensor(
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
