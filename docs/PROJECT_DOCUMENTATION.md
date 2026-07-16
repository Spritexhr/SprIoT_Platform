# 物联网控制平台项目文档

本文件为物联网控制平台的完整设计与使用说明文档，涵盖从快速上手到系统架构设计的核心内容。

---

## 第一部分：快速开始

### 1.1 项目简介
本项目是一个全栈物联网（IoT）控制系统，旨在提供一套完整的传感器数据采集、设备控制及自动化运维解决方案。
*   **后端**：基于 Django 6.0 构建的 RESTful API，集成了 MQTT 客户端和自动化规则引擎。
*   **前端**：基于 Vue 3 + Vite 构建的响应式监控仪表盘，支持实时数据展示和设备交互。
*   **硬件**：以 Wemos D1 (ESP8266) 为核心控制器，支持多种传感器（温湿度、光电、旋转等）和执行器（LED、舵机等）。
*   **通信**：采用工业级 MQTT 协议，实现低延迟、高可靠的双向异步通信。

### 1.2 硬件准备
为了运行完整的系统，建议准备以下硬件组件：
*   **主控板**：Wemos D1 Mini (ESP8266)
*   **输入器（传感器）**：
    *   DHT11/DHT22 温湿度传感器
    *   H2010 光电开关（径向计数模块）
    *   旋转编码器/传感器
    *   触摸传感器/开关
*   **输出器（执行器）**：
    *   LED 灯（基础引脚控制）
    *   SG90 舵机
    *   继电器模块（可选）
*   **基础配件**：杜邦线、面包板、Micro USB 数据线。

硬件接线参考 `hardware/wemos-d1/` 目录下各设备的 `.ino` 文件注释，详见 [hardware/README.md](../hardware/README.md)。

### 1.3 一键部署
项目支持使用 Docker Compose 进行容器化一键部署。

#### 1.3.1 环境要求
*   Docker & Docker Compose
*   MQTT Broker（如 EMQX 或 Mosquitto，建议部署在本机或可访问的云端）

#### 1.3.2 部署步骤
1.  **克隆项目并进入根目录**：
    ```bash
    cd graduation_thesis
    ```
2.  **配置环境变量**：
    复制 `.env.example` 为 `.env` 并根据实际情况修改（特别是数据库密码和 MQTT 服务器地址）。
    ```bash
    cp .env.example .env
    ```
3.  **启动容器**：
    ```bash
    docker compose up -d --build
    ```
4.  **初始化数据库**：
    ```bash
    docker compose exec backend python manage.py migrate
    docker compose exec backend python manage.py createsuperuser
    ```
5.  **访问系统**：
    *   前端界面：`http://localhost:48080`
    *   管理后台：`http://localhost:48081/admin/`

---

## 第二部分：项目设计

### 2.1 总体架构
系统采用分层架构设计，确保各组件之间的解耦与高效协作：

1.  **硬件层 (Hardware Layer)**：ESP8266 固件负责采集环境数据并执行控制指令，通过 Wi-Fi 连接网络。
2.  **通信层 (Messaging Layer)**：MQTT Broker 充当消息中转站，处理所有设备与服务器之间的异步消息推送。
3.  **服务层 (Service Layer)**：
    *   **MQTT Service**：持久化运行的后台进程，负责订阅传感器数据并发布控制指令。
    *   **Automation Engine**：基于自定义脚本的规则引擎，根据实时数据自动触发设备操作。
    *   **REST API**：为前端提供数据接口、用户鉴权和设备管理功能。
4.  **展示层 (Presentation Layer)**：单页面应用（SPA），提供直观的数据可视化图表和操作按钮。

### 2.2 通信协议规范 (核心)
系统通信严格遵循基于主题（Topic）隔离的 MQTT 协议规范。

#### 2.2.1 主题结构
*   **传感器数据上报**：`iot/sensors/{sensor_id}/data`
*   **传感器状态上报**：`iot/sensors/{sensor_id}/status`
*   **传感器控制/配置**：`iot/sensors/{sensor_id}/control`
*   **设备（执行器）状态反馈**：`iot/devices/{device_id}/status`
*   **设备（执行器）控制指令**：`iot/devices/{device_id}/control`

#### 2.2.2 消息格式 (JSON)

**1. 传感器数据上报 (Data Payload)**：
```json
{
  "sensor_id": "DHT11-WEMOS-001",
  "data": {
    "temperature": 25.5,
    "humidity": 60.0
  },
  "timestamp": 1770733931
}
```

**2. 执行器状态反馈 (Status Payload)**：
```json
{
  "device_id": "LED-01",
  "status": {
    "power_state": true,
    "brightness": 80
  },
  "timestamp": 1770733935
}
```

**3. 控制指令 (Control Payload)**：
```json
{
  "command": "set_brightness",
  "parameters": {
    "val": 100
  }
}
```

### 2.3 数据库建模
系统使用关系型数据库存储静态配置与动态记录，核心模型设计如下：

#### 2.3.1 传感器模块 (Sensors)
*   **SensorType**：定义传感器元数据（如 DHT11），包含 `data_fields`（上报字段列表）和 `commands`（支持的远程指令）。
*   **Sensor**：传感器实例，记录 `sensor_id`、位置、在线状态及所属类型。
*   **SensorData**：历史数据流水表，存储每次上报的 JSON 数据和时间戳。
*   **SensorStatusCollection**：存储传感器自身的运行状态（如心跳间隔、布防状态）。

#### 2.3.2 设备模块 (Devices)
*   **DeviceType**：定义执行器元数据（如 SG90 舵机），包含 `config_parameters` 和可用控制命令集。
*   **Device**：设备实例，关联 MQTT 控制主题与状态反馈主题。
*   **DeviceStatusCollection**：设备状态和事件的历史记录。

#### 2.3.3 自动化模块 (Automation)
*   **AutomationRule**：自动化逻辑的核心。
    *   `script`：一段 Python 脚本，支持 `loop()` 循环逻辑。
    *   `device_list`：规则关联的设备与传感器 ID 列表。
    *   `is_launched`：标识规则是否在后台引擎中运行。
    *   `poll_interval`：脚本执行的检查频率。
*   **ControlScheme**：绑定 Project 成员的双位、PI、PID 结构化闭环控制。

#### 2.3.4 项目/场景模块 (Projects)
*   **Project**：工业装置、智能家居等业务场景的顶层容器。
*   **ProjectSection**：项目内的房间、区域或工段。
*   **ProjectSensorMember / ProjectDeviceMember**：在不污染主模型的前提下保存位号、阈值、单位等场景元数据。
*   **ProjectView**：卡片、P&ID、时序趋势和自动化控制视图。

---

## 第三部分：文档索引

### 设计文档

| 文档 | 说明 |
|------|------|
| [Django 模型设计](backend/backend_design/djange_models_design.md) | Sensor/Device、Project 场景成员、AutomationRule、ControlScheme 与删除语义 |
| [MQTT 服务设计](backend/backend_design/mqtt_service_design.md) | mqtt_service、BaseCommandSendService 基类、handler 架构、自动重连 |
| [自动化规则设计](backend/backend_design/AutomationRules_design.md) | engine、head_files、安全沙箱、脚本执行流程与完整示例 |
| [Project 场景模块设计](backend/backend_design/project_design.md) | 项目/房间/成员模型、实时通道、视图与控制方案 |
| [平台配置设计](backend/backend_design/platform_settings_design.md) | PlatformConfig、安全配置、分批清理 |
| [日志系统设计](backend/backend_design/logsystem_design.md) | 日志配置、按模块分离 |

### 使用指南

| 文档 | 说明 |
|------|------|
| [Django 模型使用](backend/backend_user_guide/django_models_guide.md) | Shell 中 CRUD、关联查询 |
| [MQTT 服务使用](backend/backend_user_guide/mqtt_service_guide.md) | 命令发送、手动初始化、自动重连 |
| [自动化规则脚本](backend/backend_user_guide/AutomationRules_guide.md) | 编写符合规范的自动化脚本 |
| [Project 场景使用](backend/backend_user_guide/project_guide.md) | 创建场景、点位绑定、视图、P&ID 和控制方案 |
| [平台配置使用](backend/backend_user_guide/platform_settings_guide.md) | 配置管理、健康检查、数据清理 |
| [日志系统使用](backend/backend_user_guide/logsystem_guide.md) | 查看日志、级别、排查 |

### 更新日志

| 版本 | 说明 |
|------|------|
| [0.11](update_notes/0.11_update_notes.md) | MQTT 单 runner、自动化执行隔离、查询性能、在线状态和部署可靠性加固 |
| [0.10](update_notes/0.10_update_notes.md) | Project/场景原生化、四类视图、结构化控制与架构清理 |
| [0.9](update_notes/0.9_update_notes.md) | 自动化规则引擎、脚本编辑器与设备选择器升级 |
| [0.8](update_notes/0.8_update_notes.md) | Django Channels、Redis 与全平台 WebSocket 实时化 |
| [0.7](update_notes/0.7_update_notes.md) | 平台配置统一、首次部署引导与 Token 主动续期 |
| [0.6](update_notes/0.6_update_notes.md) | 在线状态、Admin 一致性与卡片排序修复 |
| [0.5](update_notes/0.5_update_notes.md) | 插件系统与 data_viz 数据可视化插件 |
| [0.4](update_notes/0.4_update_notes.md) | BaseCommandSendService 重构、MQTT 自动重连、安全配置、健康检查 |
| [0.3](update_notes/0.3_update_notes.md) | 后台调度器、自动化规则轮询执行 |
| [0.2](update_notes/0.2_update_notes.md) | MQTT 服务架构等 |

### 部署与硬件

| 文档 | 说明 |
|------|------|
| [部署前准备](deployment/before_deploy.md) | EMQX 安装、环境配置 |
| [Docker 部署](deployment/docker.md) | 容器化一键部署 |
| [非 Docker 部署](deployment/not_docker.md) | 传统方式部署 |
| [硬件程序设计](hardware/hardware_code_design.md) | MQTT 连接、form 文件作用 |
| [嵌入式编写指南](hardware/hardware_guide.md) | 符合后端规范的固件编写 |

---
*文档更新日期：2026年7月*
