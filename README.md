# SprIoT_Platform

SprIoT_Platform 是一个全栈物联网控制系统，支持传感器数据采集、执行器设备控制与自动化规则联动。
---

## 项目结构

```
iot_control_platform_release_version/
├── iot_control_platform/   # Django 后端（REST API + MQTT）
├── frontend/               # Vue 3 前端（监控仪表盘）
├── mcp/                    # AI Agent 的标准 MCP 操作入口
├── hardware/               # 嵌入式固件（Wemos D1 / ESP8266）
├── docs/                   # 项目文档
└── README.md               # 本文件



```

---

## 快速开始

### 1. 部署前准备

参考 [docs/deployment/before_deploy.md](docs/deployment/before_deploy.md)，完成：

- 安装 EMQX（MQTT Broker）
- 安装 Python 3.10+、Node.js 18+（前端开发时）

### 2. 部署方式

**Docker（推荐）：**

```bash
cp .env.example .env
# 编辑 .env，配置 DB_PASSWORD、MQTT_BROKER 等
docker compose up -d --build
docker compose exec backend python manage.py createsuperuser
```

访问：http://localhost:48080

**非 Docker：**

参考 [docs/deployment/not_docker.md](docs/deployment/not_docker.md)，分别启动后端与前端。

### 3. 访问系统

- **前端界面**：http://localhost:48080（或 5173 开发模式）
- **Django Admin**：http://localhost:48081/admin/（Docker 部署时直连后端）

---

## 技术栈

| 层级 | 技术 |
|-----|------|
| 后端 | Django 6、Django REST Framework、paho-mqtt |
| 前端 | Vue 3、Vite、Element Plus、Pinia |
| 通信 | MQTT（EMQX） |
| 硬件 | Wemos D1（ESP8266）、Arduino |

---

## 文档索引

| 类别 | 文档 |
|-----|------|
| GitHub 发布清单 | [docs/GITHUB_RELEASE_CHECKLIST.md](docs/GITHUB_RELEASE_CHECKLIST.md) |
| 项目概述 | [docs/PROJECT_DOCUMENTATION.md](docs/PROJECT_DOCUMENTATION.md) |
| 文档中心 | [docs/README.md](docs/README.md) |
| 部署 | [before_deploy](docs/deployment/before_deploy.md) · [Docker](docs/deployment/docker.md) · [非 Docker](docs/deployment/not_docker.md) |
| 后端设计 | [Django 模型](docs/backend/backend_design/djange_models_design.md) · [MQTT 服务](docs/backend/backend_design/mqtt_service_design.md) · [自动化规则](docs/backend/backend_design/AutomationRules_design.md) · [Project 场景](docs/backend/backend_design/project_design.md) |
| 使用指南 | [Project 场景](docs/backend/backend_user_guide/project_guide.md) |
| MCP 与 AI Agent | [MCP 基础、架构、运行与扩展指南](docs/mcp/mcp_agent_guide.md) |
| 硬件 | [硬件设计](docs/hardware_code/hardware_code_design.md) · [嵌入式编写指南](docs/hardware_code/hardware_guide.md) |

---

## 子项目说明

| 目录 | 说明 |
|-----|------|
| [iot_control_platform](iot_control_platform/README.md) | Django 后端，设备/传感器/自动化 API |
| [frontend](frontend/README.md) | Vue 前端，仪表盘与设备管理界面 |
| [mcp](mcp/README.md) | AI Agent 读取、控制和管理 SprIoT_Platform 的 MCP 服务 |
| [hardware](hardware/README.md) | Wemos D1 嵌入式固件与接线说明 |
