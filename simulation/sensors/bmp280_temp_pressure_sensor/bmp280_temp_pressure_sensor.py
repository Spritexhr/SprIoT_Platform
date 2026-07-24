"""
虚拟 BMP280 温度气压传感器 —— 复刻
hardware/wemos-d1/sensors/BPM280_temp_pressure_sensor/bmp280_temp_pressure_sensor_v3.ino

数据字段：temperature (°C)，pressure (hPa，单位与 BMP280 硬件一致，101325 Pa ≈ 1013.25 hPa)
支持命令：enable / disable / set_interval / set_data_interval / set_status_interval
"""
import argparse
import logging
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common.mqtt_node import MqttNode
from common.schema import ParamSpec
from common.waveforms import build_waveform_map

log = logging.getLogger(__name__)


DEFAULT_WAVEFORMS = {
    "temperature": {
        "type": "sine",
        "min": 18.0,
        "max": 28.0,
        "period": 3600,
        "jitter": 0.2,
    },
    "pressure": {
        "type": "random_walk",
        "start": 1013.25,
        "step": 0.3,
        "bounds": [995.0, 1030.0],
    },
}


class BMP280TempPressureSensor(MqttNode):
    NODE_TYPE = "sensor"
    ID_FIELD = "sensor_id"
    LABEL = "BMP280 温度气压传感器 (hPa)"

    DEFAULT_SAMPLING_INTERVAL = 60
    DEFAULT_STATUS_REPORT_INTERVAL = 120

    TEMPERATURE_PRECISION = 1
    PRESSURE_PRECISION = 2

    PARAMS_SCHEMA = [
        ParamSpec("sampling_interval", "float", label="采样间隔(秒)",
                  default=DEFAULT_SAMPLING_INTERVAL, min=1, max=86400),
        ParamSpec("status_report_interval", "float", label="心跳间隔(秒)",
                  default=DEFAULT_STATUS_REPORT_INTERVAL, min=5, max=86400),
        ParamSpec("waveforms", "waveform_map", label="数据波形",
                  fields=["temperature", "pressure"], default=DEFAULT_WAVEFORMS,
                  help="pressure 单位 hPa，与 BMP280 硬件一致"),
    ]

    SUPPORTED_COMMANDS = [
        {"command": "enable", "label": "启用"},
        {"command": "disable", "label": "禁用"},
        {"command": "set_interval", "label": "设置采样间隔",
         "args": [{"name": "interval", "type": "float", "min": 10, "max": 3600}]},
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

        wf_cfg = {**DEFAULT_WAVEFORMS, **(waveforms or {})}
        self._waveforms = build_waveform_map(wf_cfg)

    def build_status_payload(self) -> dict:
        return {
            "is_enabled": self.is_enabled,
            "samplingInterval": self.sampling_interval,
            "statusReportInterval": self.status_report_interval,
        }

    def read_temperature(self) -> float:
        return round(self._waveforms["temperature"].sample(), self.TEMPERATURE_PRECISION)

    def read_pressure(self) -> float:
        return round(self._waveforms["pressure"].sample(), self.PRESSURE_PRECISION)

    def handle_command(self, command: str, payload: dict, check_code: Optional[str]) -> None:
        if command in ("set_interval", "set_data_interval"):
            interval = self.coerce_number(payload.get("interval"), 0.0)
            if 10 <= interval <= 3600:
                self.sampling_interval = interval
                log.info(f"[{self.node_id}] ✓ samplingInterval → {interval}s")
                self.publish_status("interval_updated", check_code)
            else:
                log.warning(f"[{self.node_id}] ✗ interval 越界（10-3600）: {interval}")

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
        import time
        now = time.time()
        if now - self._last_sample_time < self.sampling_interval:
            return

        temperature = self.read_temperature()
        pressure = self.read_pressure()
        data_msg = {
            "sensor_id": self.node_id,
            "data": {
                "temperature": temperature,
                "pressure": pressure,
            },
            "timestamp": self.now_ts(),
        }
        if self.publish_json(self.topic_data, data_msg):
            log.info(
                f"[{self.node_id}] → data temperature={temperature}°C pressure={pressure}hPa"
            )
        self._last_sample_time = now


def main():
    parser = argparse.ArgumentParser(description="虚拟 BMP280 温度气压传感器")
    parser.add_argument("--id", default="BMP280-WEMOS-001")
    parser.add_argument("--broker", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--sampling-interval", type=float,
                        default=BMP280TempPressureSensor.DEFAULT_SAMPLING_INTERVAL)
    parser.add_argument("--status-report-interval", type=float,
                        default=BMP280TempPressureSensor.DEFAULT_STATUS_REPORT_INTERVAL)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    node = BMP280TempPressureSensor(
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
