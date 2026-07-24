"""
虚拟 DHT11 温湿度传感器 —— 复刻 hardware/wemos-d1/sensors/temp_humi_sensor/temp_humi_sensor_v3.ino

MQTT 主题：
  数据:   iot/sensors/{sensor_id}/data
  状态:   iot/sensors/{sensor_id}/status
  控制:   iot/sensors/{sensor_id}/control

支持命令：enable / disable / set_interval / set_data_interval / set_status_interval
"""
import argparse
import logging
import os
import sys
import time
from typing import Optional

# 支持作为脚本独立运行：把 simulation/ 加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common.mqtt_node import MqttNode
from common.schema import ParamSpec
from common.waveforms import build_waveform_map

log = logging.getLogger(__name__)


# 单脚本默认波形（在 config.yaml 里可逐字段覆盖）
# 字段名必须与本传感器上报的 data 字段一致：temperature / humidity
DEFAULT_WAVEFORMS = {
    "temperature": {
        "type": "sine",
        "min": 17.0,
        "max": 27.0,
        "period": 3600,
        "jitter": 0.3,
    },
    "humidity": {
        "type": "random_walk",
        "start": 55.0,
        "step": 1.5,
        "bounds": [40.0, 70.0],
    },
}


class TempHumiSensor(MqttNode):
    NODE_TYPE = "sensor"
    ID_FIELD = "sensor_id"
    LABEL = "DHT11 温湿度传感器"

    # 与 .ino 默认值一致
    DEFAULT_SAMPLING_INTERVAL = 60
    DEFAULT_STATUS_REPORT_INTERVAL = 120

    # 保留 1 位小数，与 .ino 行为一致（round(v * 10) / 10.0）
    PRECISION = 1

    PARAMS_SCHEMA = [
        ParamSpec("sampling_interval", "float", label="采样间隔(秒)",
                  default=DEFAULT_SAMPLING_INTERVAL, min=1, max=86400),
        ParamSpec("status_report_interval", "float", label="心跳间隔(秒)",
                  default=DEFAULT_STATUS_REPORT_INTERVAL, min=5, max=86400),
        ParamSpec("waveforms", "waveform_map", label="数据波形",
                  fields=["temperature", "humidity"], default=DEFAULT_WAVEFORMS,
                  help="不配置则使用默认波形；可只覆盖某个字段"),
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

        # 波形配置：未传则用模块默认；传了则与默认浅合并（允许只覆盖某个字段）
        wf_cfg = {**DEFAULT_WAVEFORMS, **(waveforms or {})}
        self._waveforms = build_waveform_map(wf_cfg)
        for field, wf in self._waveforms.items():
            log.info(f"[{node_id}] 波形 {field}: {type(wf).__name__}")

    # ============ status payload ============
    def build_status_payload(self) -> dict:
        return {
            "is_enabled": self.is_enabled,
            "samplingInterval": self.sampling_interval,
            "statusReportInterval": self.status_report_interval,
        }

    # ============ 数据生成 ============
    def read_temperature(self) -> float:
        return round(self._waveforms["temperature"].sample(), self.PRECISION)

    def read_humidity(self) -> float:
        return round(self._waveforms["humidity"].sample(), self.PRECISION)

    # ============ 控制命令 ============
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
            log.info(f"[{self.node_id}] ✓ 已启用")
            self.publish_status("sensor_enabled", check_code)

        elif command == "disable":
            self.is_enabled = False
            log.info(f"[{self.node_id}] ✓ 已禁用")
            self.publish_status("sensor_disabled", check_code)

        else:
            log.warning(f"[{self.node_id}] ⚠ 未知命令: {command}")

    # ============ 主循环 hook：周期发数据 ============
    def on_tick(self) -> None:
        if not self.is_enabled:
            return
        now = time.time()
        if now - self._last_sample_time < self.sampling_interval:
            return

        temperature = self.read_temperature()
        humidity = self.read_humidity()
        data_msg = {
            "sensor_id": self.node_id,
            "data": {
                "temperature": temperature,
                "humidity": humidity,
            },
            "timestamp": self.now_ts(),
        }
        if self.publish_json(self.topic_data, data_msg):
            log.info(
                f"[{self.node_id}] → data temperature={temperature}°C humidity={humidity}%"
            )
        self._last_sample_time = now


def main():
    parser = argparse.ArgumentParser(description="虚拟 DHT11 温湿度传感器")
    parser.add_argument("--id", default="DHT11-WEMOS-001", help="sensor_id")
    parser.add_argument("--broker", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--sampling-interval", type=float, default=TempHumiSensor.DEFAULT_SAMPLING_INTERVAL)
    parser.add_argument("--status-report-interval", type=float, default=TempHumiSensor.DEFAULT_STATUS_REPORT_INTERVAL)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    node = TempHumiSensor(
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
        log.info("⚠ Ctrl-C，停止中…")
        node.stop()


if __name__ == "__main__":
    main()
