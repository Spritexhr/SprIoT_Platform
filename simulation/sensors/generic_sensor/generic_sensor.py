"""
通用虚拟传感器（声明式）—— 数据字段完全由配置定义，新增传感器零代码

manifest 示例：
  - module: generic_sensor
    id: GEN-PH-001
    sampling_interval: 15
    fields:
      ph:        {waveform: {type: random_walk, start: 7.0, step: 0.05, bounds: [6, 8]}, precision: 2, unit: pH}
      turbidity: {waveform: {type: uniform, min: 1, max: 5}, precision: 1, unit: NTU}

协议 envelope 与 temp_humi_sensor 等"真"传感器完全一致（Django 侧无法区分）：
  数据:   iot/sensors/{sensor_id}/data    {"sensor_id", "data": {字段: 值}, "timestamp"}
  状态:   iot/sensors/{sensor_id}/status  含 is_enabled / samplingInterval / statusReportInterval
  控制:   iot/sensors/{sensor_id}/control enable / disable / set_interval / set_data_interval / set_status_interval

unit 仅作 GUI 展示元数据，不进 MQTT payload（与硬件协议保持一致）。
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
from common.waveforms import build_waveform

log = logging.getLogger(__name__)

DEFAULT_PRECISION = 2


class GenericSensor(MqttNode):
    NODE_TYPE = "sensor"
    ID_FIELD = "sensor_id"
    LABEL = "通用传感器（自定义字段）"

    DEFAULT_SAMPLING_INTERVAL = 30
    DEFAULT_STATUS_REPORT_INTERVAL = 120

    PARAMS_SCHEMA = [
        ParamSpec("sampling_interval", "float", label="采样间隔(秒)",
                  default=DEFAULT_SAMPLING_INTERVAL, min=1, max=86400),
        ParamSpec("status_report_interval", "float", label="心跳间隔(秒)",
                  default=DEFAULT_STATUS_REPORT_INTERVAL, min=5, max=86400),
        ParamSpec("fields", "fields_map", label="数据字段", required=True,
                  help="每个字段配置 waveform（必填）、precision(0-6, 默认2)、unit(仅展示)"),
    ]

    SUPPORTED_COMMANDS = [
        {"command": "enable", "label": "启用"},
        {"command": "disable", "label": "禁用"},
        {"command": "set_interval", "label": "设置采样间隔",
         "args": [{"name": "interval", "type": "float", "min": 5, "max": 3600}]},
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
        fields: Optional[dict] = None,
    ):
        super().__init__(
            node_id=node_id,
            broker=broker,
            port=port,
            username=username,
            password=password,
            status_report_interval=status_report_interval,
        )
        if not fields:
            raise ValueError(f"[{node_id}] generic_sensor 必须配置 fields（至少一个数据字段）")

        self.topic_data = f"iot/sensors/{node_id}/data"
        self.sampling_interval = sampling_interval
        self.is_enabled = True
        self._last_sample_time = 0.0

        # {字段名: (Waveform, precision)}
        self._fields = {}
        for name, cfg in fields.items():
            wf = build_waveform(cfg["waveform"])
            precision = int(cfg.get("precision", DEFAULT_PRECISION))
            self._fields[name] = (wf, precision)
            log.info(f"[{node_id}] 字段 {name}: {type(wf).__name__} 精度={precision}")

    def build_status_payload(self) -> dict:
        return {
            "is_enabled": self.is_enabled,
            "samplingInterval": self.sampling_interval,
            "statusReportInterval": self.status_report_interval,
        }

    def _read_all(self) -> dict:
        return {
            name: round(wf.sample(), precision)
            for name, (wf, precision) in self._fields.items()
        }

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

        data = self._read_all()
        data_msg = {
            "sensor_id": self.node_id,
            "data": data,
            "timestamp": self.now_ts(),
        }
        if self.publish_json(self.topic_data, data_msg):
            log.info(f"[{self.node_id}] → data {data}")
        self._last_sample_time = now


def main():
    parser = argparse.ArgumentParser(
        description="通用虚拟传感器（字段由 --field 定义）",
        epilog='示例: --field "ph:random_walk:start=7,step=0.05,lo=6,hi=8" '
               '--field "level:sine:min=0,max=100,period=600"',
    )
    parser.add_argument("--id", default="GEN-SENSOR-001")
    parser.add_argument("--broker", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--sampling-interval", type=float,
                        default=GenericSensor.DEFAULT_SAMPLING_INTERVAL)
    parser.add_argument("--status-report-interval", type=float,
                        default=GenericSensor.DEFAULT_STATUS_REPORT_INTERVAL)
    parser.add_argument("--field", action="append", default=None,
                        help="字段定义 name:waveform_type:k=v,k=v（可重复）；"
                             "random_walk 用 lo/hi 表示 bounds 两端")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # 命令行字段定义解析（manifest/webui 用结构化配置，这里只为单脚本调试便利）
    fields = {}
    for spec in args.field or ["demo:sine:min=0,max=100,period=300"]:
        try:
            name, wf_type, kv_str = spec.split(":", 2)
            params = {}
            for kv in kv_str.split(","):
                k, v = kv.split("=", 1)
                params[k.strip()] = float(v)
            if "lo" in params or "hi" in params:
                params["bounds"] = [params.pop("lo", 0.0), params.pop("hi", 100.0)]
            fields[name.strip()] = {"waveform": {"type": wf_type.strip(), **params}}
        except ValueError:
            parser.error(f"--field 格式错误: {spec!r}（应为 name:type:k=v,k=v）")

    node = GenericSensor(
        node_id=args.id,
        broker=args.broker,
        port=args.port,
        username=args.username,
        password=args.password,
        sampling_interval=args.sampling_interval,
        status_report_interval=args.status_report_interval,
        fields=fields,
    )
    try:
        node.run()
    except KeyboardInterrupt:
        node.stop()


if __name__ == "__main__":
    main()
