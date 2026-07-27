"""
通用虚拟设备（声明式）—— 状态字段完全由配置定义，简单执行器零代码

manifest 示例：
  - module: generic_device
    id: GEN-VALVE-001
    state_fields:
      valve_open: {type: bool, initial: false}
      opening:    {type: float, initial: 0, min: 0, max: 100}

协议 envelope 与 sg90_servo 等"真"设备一致：
  状态:  iot/devices/{device_id}/status   status 含全部 state_fields + statusReportInterval
  控制:  iot/devices/{device_id}/control

支持命令：
  set_state {field, val}      —— 设置某个状态字段（bool 接受 true/false/1/0，float 自动夹到 min/max）
  set_opening {value|val}     —— set_state 的工业阀门别名，写 opening
  set_duty {value|val}        —— set_state 的热负荷别名，写 duty
  set_setpoint {value|val}    —— set_state 的控制器设定值别名，写 setpoint
  current_status              —— 上报当前状态
  set_status_interval {interval}

复杂行为（如泵的功率渐变）请写专门的节点类（参考 devices/pump）。
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


class GenericDevice(MqttNode):
    NODE_TYPE = "device"
    ID_FIELD = "device_id"
    LABEL = "通用设备（自定义状态）"

    DEFAULT_STATUS_REPORT_INTERVAL = 60

    # 与主平台 DeviceType.commands 的常用工业命令对齐。只有 manifest 声明了
    # 对应状态字段时才接受，避免一个阀门节点意外吞掉 set_duty 等无关命令。
    COMMAND_FIELD_ALIASES = {
        "set_opening": "opening",
        "set_duty": "duty",
        "set_setpoint": "setpoint",
    }

    PARAMS_SCHEMA = [
        ParamSpec("status_report_interval", "float", label="心跳间隔(秒)",
                  default=DEFAULT_STATUS_REPORT_INTERVAL, min=5, max=86400),
        ParamSpec("state_fields", "state_fields", label="状态字段", required=True,
                  help="每个字段配置 type(bool/float)、initial；float 可加 min/max 限幅"),
    ]

    SUPPORTED_COMMANDS = [
        {"command": "set_state", "label": "设置状态",
         "args": [{"name": "field", "type": "str"}, {"name": "val", "type": "str"}]},
        {"command": "set_opening", "label": "设置阀门开度",
         "args": [{"name": "value", "type": "float"}]},
        {"command": "set_duty", "label": "设置热/冷负荷",
         "args": [{"name": "value", "type": "float"}]},
        {"command": "set_setpoint", "label": "设置控制器设定值",
         "args": [{"name": "value", "type": "float"}]},
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
        state_fields: Optional[dict] = None,
    ):
        super().__init__(
            node_id=node_id,
            broker=broker,
            port=port,
            username=username,
            password=password,
            status_report_interval=status_report_interval,
        )
        if not state_fields:
            raise ValueError(f"[{node_id}] generic_device 必须配置 state_fields（至少一个状态字段）")

        # {字段名: 配置}，self.states 保存当前值
        self._field_cfgs = {}
        self.states = {}
        for name, cfg in state_fields.items():
            cfg = dict(cfg or {})
            f_type = cfg.get("type", "float")
            if f_type == "bool":
                value = bool(cfg.get("initial", False))
            else:
                value = float(cfg.get("initial", 0.0))
            self._field_cfgs[name] = cfg
            self.states[name] = value
            log.info(f"[{node_id}] 状态字段 {name} ({f_type}) = {value}")

    def build_status_payload(self) -> dict:
        payload = dict(self.states)
        payload["statusReportInterval"] = self.status_report_interval
        return payload

    def _coerce(self, name: str, raw) -> Optional[object]:
        """把命令里的 val 转换成字段类型；非法返回 None"""
        cfg = self._field_cfgs[name]
        if cfg.get("type", "float") == "bool":
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, (int, float)) and raw in (0, 1):
                return bool(raw)
            if isinstance(raw, str) and raw.lower() in ("true", "false", "1", "0", "on", "off"):
                return raw.lower() in ("true", "1", "on")
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        lo, hi = cfg.get("min"), cfg.get("max")
        if lo is not None:
            value = max(float(lo), value)
        if hi is not None:
            value = min(float(hi), value)
        return value

    def _update_state(self, field_name: str, raw, event: str,
                      check_code: Optional[str]) -> bool:
        """校验并更新一个状态字段，成功时立即回传带 check_code 的状态。"""
        if field_name not in self.states:
            log.warning(
                f"[{self.node_id}] ✗ 未配置状态字段 {field_name!r}，"
                f"可用: {list(self.states.keys())}"
            )
            return False
        value = self._coerce(field_name, raw)
        if value is None:
            log.warning(f"[{self.node_id}] ✗ {field_name} 值非法: {raw!r}")
            return False
        self.states[field_name] = value
        log.info(f"[{self.node_id}] ✓ {field_name} → {value}")
        self.publish_status(event, check_code)
        return True

    def handle_command(self, command: str, payload: dict, check_code: Optional[str]) -> None:
        if command == "set_state":
            field_name = payload.get("field")
            self._update_state(field_name, payload.get("val"), "state_updated", check_code)

        elif command in self.COMMAND_FIELD_ALIASES:
            field_name = self.COMMAND_FIELD_ALIASES[command]
            # 主平台模板使用 value；Web UI/手工命令也兼容 val。
            raw = payload.get("value", payload.get("val"))
            self._update_state(field_name, raw, f"{field_name}_updated", check_code)

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


def main():
    parser = argparse.ArgumentParser(
        description="通用虚拟设备（状态字段由 --state 定义）",
        epilog='示例: --state "valve_open:bool:initial=0" --state "opening:float:initial=0,min=0,max=100"',
    )
    parser.add_argument("--id", default="GEN-DEVICE-001")
    parser.add_argument("--broker", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--status-report-interval", type=float,
                        default=GenericDevice.DEFAULT_STATUS_REPORT_INTERVAL)
    parser.add_argument("--state", action="append", default=None,
                        help="状态字段定义 name:type:k=v,k=v（可重复）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    state_fields = {}
    for spec in args.state or ["switch:bool:initial=0"]:
        try:
            name, f_type, kv_str = spec.split(":", 2)
            cfg = {"type": f_type.strip()}
            for kv in kv_str.split(","):
                k, v = kv.split("=", 1)
                cfg[k.strip()] = bool(int(v)) if f_type.strip() == "bool" else float(v)
            state_fields[name.strip()] = cfg
        except ValueError:
            parser.error(f"--state 格式错误: {spec!r}（应为 name:type:k=v,k=v）")

    node = GenericDevice(
        node_id=args.id,
        broker=args.broker,
        port=args.port,
        username=args.username,
        password=args.password,
        status_report_interval=args.status_report_interval,
        state_fields=state_fields,
    )
    try:
        node.run()
    except KeyboardInterrupt:
        node.stop()


if __name__ == "__main__":
    main()
