# 虚拟传感器与设备仿真（simulation 模块）

复刻 `hardware/wemos-d1/` 下各 `.ino` 固件的 MQTT 行为，用 Python 模拟一台台"软件版的硬件"——同样的主题、同样的 payload、同样的 check_code 回传协议，但跑在 Mac/Linux 上而不是 ESP8266 上。

> 代码位于仓库根目录的 `simulation/`，与 `iot_control_platform/` Django 后端**完全解耦**。仿真器只是一组 MQTT 客户端，通过 broker 与后端通信，不依赖任何 Django 代码或数据库。
>
> 端到端验证步骤见 [测试验证教程](testing_guide.md)。

---

## 目录结构

```
simulation/
├── README.md                 指向本文档的入口说明
├── requirements.txt          paho-mqtt + PyYAML
├── config.yaml.example       broker 配置模板（提交到 git）
├── config.yaml               broker 真实配置（gitignore，复制 .example 后改）
├── run.py                    统一启动器，加载 manifests/ 下的清单（节点模块自动发现）
├── manifests/                节点清单目录（提交到 git，按项目分文件）
│   ├── default.yaml          默认清单：常规测试节点
│   ├── st_plant.yaml         苯乙烯(ST)装置清单：6 路传感器 + 2 台泵
│   └── luyben_eb.yaml        Luyben 乙苯清单：38 路传感器 + 15 个执行器
├── common/
│   ├── mqtt_node.py          基类：连接/重连/订阅/心跳/check_code 回传
│   ├── waveforms.py          数据波形 + WAVEFORM_SCHEMAS（参数元数据与校验）
│   ├── registry.py           自动发现：扫描 sensors/ devices/ 子目录构建注册表
│   └── schema.py             ParamSpec：节点参数 schema（manifest 校验 + webui 表单）
├── sensors/                  传感器节点（上报 data，可能上报 status）
│   ├── temp_humi_sensor/
│   ├── bmp280_temp_pressure_sensor/
│   ├── rotation_sensor/
│   ├── touch_sensor_switch/
│   ├── radial_counting_module/
│   ├── flow_sensor/
│   └── generic_sensor/       ★ 声明式通用传感器：字段全由配置定义，零代码
├── devices/                  设备节点（执行器，只上报 status，响应命令）
│   ├── sg90_servo/
│   ├── pin_control/
│   ├── pump/
│   └── generic_device/       ★ 声明式通用设备：状态字段全由配置定义
└── webui/                    ★ 独立 Web 管理界面（FastAPI + Vue 单页，详见下文）
    ├── server.py             API + WebSocket + 静态页托管
    ├── db.py                 SQLite 数据层（节点配置的 source of truth）
    ├── manifest_io.py        DB ↔ manifest YAML 双向转换
    ├── process_manager.py    run.py 子进程启停 + 日志跟踪
    ├── mqtt_monitor.py       MQTT 实时监控 + 节点级命令
    ├── static/               无构建前端（vendored Vue 3）
    └── runtime/              sqlite/临时 manifest/日志（gitignore）
```

每个节点是一个独立目录，内含 `<module>.py`（节点实现）和若干 `mqtt_*.json`（payload 样例，对照硬件 form 文件）。

---

## 环境准备

```bash
# 创建独立 conda 环境
conda create -n simulation_env python=3.11 -y
conda activate simulation_env

# 安装依赖
pip install -r simulation/requirements.txt
```

依赖只有 `paho-mqtt`（MQTT 客户端）和 `PyYAML`（解析配置）。

---

## 配置

配置分两层：

- **`config.yaml`** —— 只放 broker 地址/默认账号（gitignore，每人本地一份）。首次使用 `cp simulation/config.yaml.example simulation/config.yaml` 后改 broker.host。
- **`manifests/*.yaml`** —— 节点清单，按项目/场景分文件（进 git，团队共享）。`run.py` 根据 `--manifest` 决定加载哪一份（或哪几份）。

最小 `config.yaml`：
```yaml
broker:
  host: 116.62.68.29       # 你的 EMQX 地址
  port: 1883
  username: ""             # broker 级默认账号，留空表示匿名
  password: ""
```

最小 `manifests/<name>.yaml`：
```yaml
name: <name>
description: 给团队成员的说明
nodes:
  - module: temp_humi_sensor
    id: DHT11-WEMOS-001
    sampling_interval: 10
    status_report_interval: 30
```

### 节点级 username/password 覆盖

`broker.username/password` 是所有节点的默认账号。如果 EMQX 启用了 ACL，可以在 manifest 里给单个节点指定自己的账号：

```yaml
nodes:
  - module: temp_humi_sensor
    id: DHT11-WEMOS-001
    # 不写 username → 用 broker 级默认（匿名）

  - module: temp_humi_sensor
    id: DHT11-WEMOS-002
    username: sensor_002    # 这个节点用专属账号
    password: "secret123"
```

---

## 启动方式

### 方式 1：批量启动（推荐）

`run.py` 从 `config.yaml` 拿 broker，从 `manifests/*.yaml` 拿节点列表，每个节点起一个独立线程，client_id = `WemosD1-<id>`（与固件一致）。

```bash
# 加载默认清单 manifests/default.yaml
python simulation/run.py

# 加载指定清单（manifest 名 = 文件名去掉 .yaml）
python simulation/run.py --manifest st_plant
python simulation/run.py -m st_plant            # 短写法
python simulation/run.py -m luyben_eb            # Luyben 乙苯教学场景

# 同时加载多份清单（会合并并检测重复的 module+id）
python simulation/run.py -m default -m st_plant

# 也可以直接指定 yaml 路径
python simulation/run.py -m ./my_custom_manifest.yaml

# 只校验清单（不连 broker 不启动节点；CI / 改完配置先检查用）
python simulation/run.py --check -m st_plant
python simulation/run.py --check -m luyben_eb
```

`Ctrl-C` 会优雅停止所有节点。

启动前 `run.py` 会按各节点类声明的参数 schema **聚合校验整份清单**，任何字段类型/范围/波形参数错误都会一次性列出并定位到"哪份清单第几个节点的哪个字段"，校验不通过不启动。

#### 清单的设计意图

- **一个项目一份清单**：苯乙烯装置写在 `st_plant.yaml`、常规测试写在 `default.yaml`、有新场景就新建一份。
- **按需启动**：跑苯乙烯装置时只需 `-m st_plant`，不会被无关节点污染。
- **组合启动**：调试时可以叠加，比如 `-m default -m st_plant`。重复的 `(module, id)` 会直接报错，避免 client_id 冲突。
- **共享进 git**：manifest 不含敏感凭据（凭据走 broker 级默认或节点级覆盖），团队成员 clone 下来就能跑。

`luyben_eb.yaml` 使用 `FI0101`、`PI0101`、`TI0101`、`LI0101` 等正式场景 ID，
不要与同样占用这些 ID 的 `test-sample.yaml` 同时启动，否则 MQTT client_id 和主题会冲突。

### 方式 2：单独跑一个节点

每个节点的 `.py` 都可以独立运行（脚本顶部已把 `simulation/` 加入 `sys.path`），使用 `argparse` 接受命令行参数。这种方式不读 `config.yaml`、不读 manifest，适合调试单个节点或者快速验证。注意路径是 `simulation/<sensors|devices>/<module>/<module>.py` 的嵌套结构：

```bash
# 温湿度
python simulation/sensors/temp_humi_sensor/temp_humi_sensor.py \
  --id DHT11-WEMOS-001 --broker 116.62.68.29 \
  --sampling-interval 5 --status-report-interval 15

# 舵机
python simulation/devices/sg90_servo/sg90_servo.py \
  --id sg90_001 --broker 116.62.68.29

# 水泵
python simulation/devices/pump/pump.py \
  --id pump_001 --broker 116.62.68.29 --max-power-kw 20
```

每个脚本 `--help` 看完整参数列表。

---

## 节点目录

### 复刻硬件（与对应 .ino 协议一致）

| Module | 默认 ID | 数据字段 | 关键命令 |
|--------|---------|---------|---------|
| `temp_humi_sensor` | DHT11-WEMOS-001 | `temperature`(°C), `humidity`(%) | enable/disable/set_data_interval/set_status_interval |
| `bmp280_temp_pressure_sensor` | BMP280-WEMOS-001 | `temperature`(°C), `pressure`(**hPa**) | 同上 |
| `rotation_sensor` | Rotation-001 | `raw`(0-1023), `position`(0-100%), `angle`(0-360°) | 同上 |
| `touch_sensor_switch` | Switch-Touch-001 | `switch`(bool)，**事件驱动**：状态翻转才发 data | enable/disable/set_status_interval |
| `radial_counting_module` | H2010-PHOTO-001 | 协议同 touch | 同上 |
| `sg90_servo` | sg90_001 | 仅 status，无 data | set_angle/current_status/set_status_interval |
| `pin_control` | potential_controler_001 | 仅 status，`level_d5/d6/d7` | high/low/high_all/low_all/current_status/set_status_interval |

### 新增节点（无对应固件）

| Module | 默认 ID | 数据字段 / 状态字段 | 关键命令 |
|--------|---------|---------|---------|
| `pump` | pump_001 | 仅 status：`power_kw`, `target_power_kw`, `is_running` | **start/stop/set_power**/current_status/set_status_interval |
| `flow_sensor` | FLOW-001 | `flow_rate`(L/min), `accumulated_volume`(L) | enable/disable/set_data_interval/set_status_interval/**reset_volume** |

### 声明式通用节点（零代码新增节点）

| Module | 说明 | 关键命令 |
|--------|------|---------|
| `generic_sensor` | 数据字段完全由配置定义：每个字段配 `waveform` + `precision` + `unit`（仅展示）。可在同一节点组合温度(°C)与压力(kPa)等多路数据；协议 envelope 与"真"传感器一致，Django 侧无法区分 | enable/disable/set_interval/set_status_interval |
| `generic_device` | 状态字段完全由配置定义：`type`(bool/float) + `initial` + `min/max` 限幅 | **set_state {field, val}**；工业别名 **set_opening / set_duty / set_setpoint**；current_status/set_status_interval |

```yaml
# 想模拟一个水质传感器？不用写任何 Python：
- module: generic_sensor
  id: GEN-WQ-001
  sampling_interval: 20
  fields:
    ph:        {waveform: {type: random_walk, start: 7.0, step: 0.05, bounds: [6.0, 8.0]}, precision: 2, unit: pH}
    turbidity: {waveform: {type: uniform, min: 1.0, max: 5.0}, precision: 1, unit: NTU}

# 一个带开度的阀门：
- module: generic_device
  id: GEN-VALVE-001
  state_fields:
    valve_open: {type: bool, initial: false}
    opening:    {type: float, initial: 0, min: 0, max: 100}
```

> 注册表由 `common/registry.py` **自动发现**（扫描 `sensors/` `devices/` 子目录，目录名 = module 名），manifest 里 `module` 字段必须是已发现的某个 key。启动日志会打印"已发现 N 个节点模块"。

---

## 配置波形

只对**传感器节点**有效（设备节点状态由命令驱动，不需要波形）。每个节点可以为每个 data 字段独立配置波形：

```yaml
- module: temp_humi_sensor
  id: DHT11-WEMOS-001
  waveforms:
    temperature: {type: sine, min: 20, max: 26, period: 3600, jitter: 0.3}
    humidity:    {type: random_walk, start: 55, step: 1.5, bounds: [40, 70]}
```

**支持的波形类型**：

| type | 参数 | 用途 |
|------|------|------|
| `sine` | `min`, `max`, `period`(秒), `jitter`(±噪声，默认 0), `phase`(初始相位偏移秒，默认 0) | 昼夜温度、潮汐等周期信号 |
| `random_walk` | `start`, `step`, `bounds`(`[lo, hi]`) | 湿度、电池电量等慢变量 |
| `uniform` | `min`, `max` | 独立采样随机数 |
| `constant` | `value` | 恒定值（开关基线、固定 setpoint） |

**用 phase 错开同型号节点**：多个同型号传感器默认会同步——比如两个 `temp_humi_sensor` 启动时 `_t0` 接近相同，正弦波相位也接近相同。通过 `phase: <秒数>` 把其中一个的相位推后，曲线就错开了：

```yaml
- module: temp_humi_sensor
  id: room_A
  waveforms:
    temperature: {type: sine, min: 20, max: 26, period: 3600, phase: 0}

- module: temp_humi_sensor
  id: room_B
  waveforms:
    temperature: {type: sine, min: 20, max: 26, period: 3600, phase: 900}  # 偏移 15 分钟
```

`manifests/st_plant.yaml` 是一个用 phase 错相、按真实工艺数值（反应器进/出口温压、进料/蒸汽流量等）配置波形的完整范例，可直接参考。

`manifests/luyben_eb.yaml` 采用 Luyben 论文稳态值附近的小幅独立正弦波：循环苯
969.4 kmol/h、新鲜苯/乙烯 630.6 kmol/h、R1 433.7 K、C1/C2 控制板温度
365.8/394.5 K、DEB 回流 251.2 kmol/h。它只提供教学信号，不建立执行器到传感器的过程耦合。

**不写 waveforms 字段**：每个传感器 `.py` 顶部都有一个 `DEFAULT_WAVEFORMS` 常量提供合理默认值，单独跑（不通过 `run.py`）时就用这套默认。

---

## 与 Django 后端对接

仿真器是一组**纯 MQTT 客户端**，对 Django 透明。Django 那一侧需要：

1. **EMQX 在线** 且 Django `mqtt_service` 已连接（`runserver` 启动时会自动连）。
2. **数据库里有匹配的 Sensor / Device 记录**：仿真器 `id` 字段必须等于 `Sensor.sensor_id` 或 `Device.device_id`，否则 Django 收到消息会因为找不到对应记录而拒绝入库。
3. **SensorType / DeviceType 的字段定义匹配**：
   - `SensorType.data_fields` 要包含仿真器上报的所有键（如 BMP280 要有 `temperature`, `pressure`）
   - `SensorType.commands` / `DeviceType.commands` 要包含你想从后端发送的命令，参考 `hardware/wemos-d1/*/mqtt_command_form.txt`

详细的端到端验证步骤见 [测试验证教程](testing_guide.md)。

---

## 添加新节点

**先问一句：真的需要写代码吗？** 如果只是"按某种波形上报几个数据字段"或"维护几个可被命令设置的状态字段"，直接用 `generic_sensor` / `generic_device` 配置即可（见上文），**零代码**。只有需要自定义行为（如泵的功率渐变、事件驱动翻转）才写新类。

写新类只需两步（注册表自动发现，**不再需要改 run.py**）：

1. 在 `sensors/` 或 `devices/` 下新建目录 `xxx/`，在其中建 `xxx.py`（目录名必须 = 文件名）和空 `__init__.py`。继承 `MqttNode`，设置 `NODE_TYPE`（`"sensor"`/`"device"`）和 `ID_FIELD`（`"sensor_id"`/`"device_id"`），实现：
   - `build_status_payload()` —— 返回 status 字典
   - `handle_command(command, payload, check_code)` —— 处理控制命令；**响应命令后必须调用 `self.publish_status(event, check_code)`**，把收到的 check_code 原样回传，否则后端 `send_command_with_make_sure` 会超时
   - `on_tick()` —— （可选）周期任务，比如发数据。基类的 tick 频率约 10 Hz

   同时建议声明三个类属性（webui 表单与 manifest 校验都靠它们）：
   - `LABEL` —— GUI 展示名
   - `PARAMS_SCHEMA` —— 构造参数的 `ParamSpec` 列表（参考 `devices/pump/pump.py`）
   - `SUPPORTED_COMMANDS` —— 支持的命令清单（GUI 渲染快捷按钮）

2. 在某份 `manifests/*.yaml` 的 `nodes:` 里加一条 `module: xxx`（或直接在 webui 里选它建节点）。`python run.py --check -m <清单>` 验证。

最简模板参考 `sensors/radial_counting_module/radial_counting_module.py`（继承 `TouchSensorSwitch`，仅几十行）。完整模板参考 `sensors/temp_humi_sensor/temp_humi_sensor.py` 或 `devices/pump/pump.py`。

---

## Web 管理界面（webui）

`simulation/webui/` 是一个**独立的轻量 Web 服务**（FastAPI + 无构建 Vue 单页），与主平台 Django/Vue/登录体系完全无关，定位是本地开发工具。

```bash
pip install -r simulation/webui/requirements.txt      # fastapi + uvicorn
cd simulation && uvicorn webui.server:app --port 8800
# 浏览器打开 http://127.0.0.1:8800
```

### 功能

| 页面 | 能做什么 |
|------|---------|
| **节点管理** | 分组（=manifest 概念）与节点 CRUD；按节点类型动态渲染参数表单；**波形参数实时预览曲线**；manifest YAML 导入/导出；"启动本组"一键拉起 |
| **运行监控** | 运行中的 run.py 子进程（pid/时长/停止）；节点实时在线状态、最新数据/状态（来自 MQTT 订阅）；按节点类型渲染**快捷命令按钮**（enable/disable/set_angle/start/stop…） |
| **日志** | run 子进程日志实时 tail（WebSocket 推送）+ 关键词过滤 + 历史分页 |
| **设置** | broker profile 管理 + 实连测试 + 一键导入旧 config.yaml |

### 数值类型约定

- 连续物理量和时间量使用浮点数：波形参数、设备功率/开度、舵机角度、采样间隔、心跳间隔都可填写小数，例如 `sampling_interval: 10.25`、`initial_angle: 90.5`。
- 离散值仍严格使用整数：MQTT 端口、数据小数位数 `precision`、数据库主键等不会接受小数。
- Web 表单、manifest 校验、单节点命令行参数和 MQTT 运行态命令使用同一套类型语义；小数不会在启动或命令处理时被截断。
- `NaN`、正负无穷和布尔值不属于合法连续数值，会在校验或运行时转换阶段被拒绝。

### 架构要点

- **SQLite 为 source of truth**（`webui/runtime/sim.db`，gitignore）：节点配置存 DB，启动时渲染成 `runtime/manifest_run_<id>.yaml` + `config_run_<id>.yaml` 再 spawn `run.py` 子进程 —— run.py 对 DB 零感知，CLI 工作流不受影响。每次启动的 manifest 快照入库，可脱离 GUI 手工复跑：
  `python run.py --config webui/runtime/config_run_42.yaml -m webui/runtime/manifest_run_42.yaml`
- **节点级控制走 MQTT 协议本身**：webui 内嵌一个 paho 客户端（client_id `sim-webui-<pid>`）订阅 `iot/+s/+/status` 与 `iot/sensors/+/data`，命令通过发布 control 主题下发——与 Django/真硬件完全相同的链路。注意这类控制是**运行态**的，子进程重启后恢复 DB 配置；要持久改动请编辑节点。
- **进程恢复**：webui 重启时按 runs 表的 pid 探活，存活的 run 自动接管（继续 tail 日志、可停止），已死的标记 exited。
- 在线判定阈值 = 该节点 `statusReportInterval × 2 + 10s`（status payload 自带间隔值）。
- broker 密码明文存于本地 sqlite（runtime/ 已 gitignore）；本工具仅限本机/内网开发环境使用，不要公网暴露。

---

## 常见问题

| 现象 | 可能原因 | 处理 |
|------|---------|------|
| `Connection refused 127.0.0.1:1883` | 单脚本没传 `--broker`，用了默认本地地址 | `--broker <你的IP>` 或改用 `run.py` 跑 |
| `未知节点模块 'xxx'` | yaml 里 module 名拼错 / 目录名≠文件名导致自动发现失败 | 对照 [节点目录](#节点目录) 表格；看启动日志"已发现 N 个节点模块"与"跳过节点模块"警告 |
| simulator 日志正常但后端没入库 | `sensor_id`/`device_id` 在 Django 里不存在 | admin 里建对应记录 |
| `send_command_with_make_sure` 超时 | check_code 没回传 | L2 抓包看 status payload 是否带 `check_code` 字段 |
| 心跳很正常但没收到 data | 该节点是事件驱动（touch/h2010）或 disabled | 等状态翻转 / 发 enable 命令 |

更多排错见 [测试验证教程](testing_guide.md) 末尾的"常见问题排查"表。
