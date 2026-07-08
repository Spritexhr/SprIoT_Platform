# 物联网控制平台 - 文档中心

本目录为项目文档库，按设计文档、使用指南、部署、硬件等分类组织。

---

## 入口文档

| 文档 | 说明 |
|------|------|
| [项目概述](./PROJECT_DOCUMENTATION.md) | 项目简介、快速开始、系统架构、通信协议、数据库设计、文档索引 |

---

## 部署

| 文档 | 说明 |
|------|------|
| [部署前准备](deployment/before_deploy.md) | EMQX 安装、Python/Node 环境、MySQL 可选、检查清单 |
| [Docker 部署](deployment/docker.md) | 使用 Docker Compose 容器化部署 |
| [非 Docker 部署](deployment/not_docker.md) | 传统方式部署（开发/生产） |

---

## 后端设计

| 文档 | 说明 |
|------|------|
| [Django 模型设计](backend/backend_design/djange_models_design.md) | Sensor/Device、Project 场景成员、AutomationRule、ControlScheme 与删除语义 |
| [MQTT 服务设计](backend/backend_design/mqtt_service_design.md) | mqtt_service、BaseCommandSendService 基类、send_service、handler 架构与自动重连 |
| [自动化规则设计](backend/backend_design/AutomationRules_design.md) | engine、head_files、脚本执行流程、安全沙箱与完整示例 |
| [Project 场景模块设计](backend/backend_design/project_design.md) | 项目/房间/成员模型、实时链路、四类视图与结构化控制 |
| [平台配置设计](backend/backend_design/platform_settings_design.md) | PlatformConfig、get_config、seed、安全配置、分批清理 |
| [插件系统设计](backend/backend_design/plugins_design.md) | plugins/ 目录约定、Plugin 登记表、发现/启用/挂载生命周期、data_viz 内置插件 |
| [日志系统设计](backend/backend_design/logsystem_design.md) | 按模块分离、轮转、Logger 配置 |

---

## 后端使用指南

| 文档 | 说明 |
|------|------|
| [Django 模型使用](backend/backend_user_guide/django_models_guide.md) | Shell 中 CRUD、关联查询、方法调用 |
| [MQTT 服务使用](backend/backend_user_guide/mqtt_service_guide.md) | 命令发送、手动初始化、自动重连、主题速览 |
| [自动化规则脚本](backend/backend_user_guide/AutomationRules_guide.md) | 编写符合规范的自动化脚本 |
| [Project 场景使用](backend/backend_user_guide/project_guide.md) | 创建场景、导入点位、配置视图、P&ID 图元与控制方案 |
| [平台配置使用](backend/backend_user_guide/platform_settings_guide.md) | 配置 CRUD、健康检查、数据清理、节流 |
| [插件开发指南](backend/backend_user_guide/plugins_guide.md) | 5 分钟添加 hello 插件、目录骨架、与前端联调、常见坑位 |
| [日志系统使用](backend/backend_user_guide/logsystem_guide.md) | 查看日志、级别、常见问题 |

---

## 硬件与嵌入式

| 文档 | 说明 |
|------|------|
| [硬件程序设计](hardware/hardware_code_design.md) | MQTT 连接流程、mqtt_command_form、mqtt_data_form、mqtt_status_form |
| [嵌入式编写指南](hardware/hardware_guide.md) | 符合后端规范的固件编写步骤与 checklist |

---

## 前端设计

| 文档 | 说明 |
|------|------|
| [前端设计说明](frontend/frontend_design.md) | 当前技术栈、路由、Project 工作台、状态管理、REST/WebSocket 与部署 |

---

## MCP 与 AI Agent

| 文档 | 说明 |
|------|------|
| [MCP 与 AI Agent 集成指南](mcp/mcp_agent_guide.md) | 面向初学者讲解 MCP 基础、Host/Client/Server、工具调用生命周期，以及本项目 MCP 的架构、功能、安全、运行、调试和扩展方法 |

---

## 仿真（虚拟传感器/设备）

| 文档 | 说明 |
|------|------|
| [仿真模块使用说明](simulation/simulation_guide.md) | 目录结构、环境、config.yaml + manifests 配置、run.py 启动、节点目录、波形、与后端对接、添加新节点 |
| [仿真端到端验证教程](simulation/testing_guide.md) | L1 日志 / L2 MQTT 抓包 / L3 Django 入库 + check_code / L4 批量启动 |

> 仿真代码位于仓库根目录 `simulation/`（与 Django 后端解耦）；`simulation/README.md` 仅留快速上手，详细说明以本目录为准。

---

## 更新日志

| 版本 | 说明 |
|------|------|
| [0.10 更新日志](update_notes/0.10_update_notes.md) | Project/场景原生化、四类视图、双位/PI/PID 控制、仿真平台与架构清理 |
| [0.9 更新日志](update_notes/0.9_update_notes.md) | 自动化规则系统升级（引擎、SensorWrapper 扩展、CodeMirror 编辑器、设备选择器）；示例文件迁入 docs |
| [0.8 更新日志](update_notes/0.8_update_notes.md) | 全平台实时化（Django Channels + Redis + WebSocket）；MQTT 客户端拆分独立进程；useWebSocket composable |
| [0.7 更新日志](update_notes/0.7_update_notes.md) | 配置系统统一（configure 命令）；首次部署引导；系统设置页重写；Token 主动续期 |
| [0.6 更新日志](update_notes/0.6_update_notes.md) | 在线状态显示修复；Admin 一致性整治；卡片拖拽排序 |
| [0.5 更新日志](update_notes/0.5_update_notes.md) | 插件系统（plugins 目录 + Plugin 登记 + sync 命令）；内置 data_viz 数据可视化插件 |
| [0.4 更新日志](update_notes/0.4_update_notes.md) | BaseCommandSendService 基类重构；MQTT 自动重连；生产安全配置；健康检查端点 |
| [0.3 更新日志](update_notes/0.3_update_notes.md) | 后台调度器；自动化规则轮询执行 |
| [0.2 更新日志](update_notes/0.2_update_notes.md) | MQTT 服务架构；自动重连 |

---

## 目录结构

```
docs/
├── README.md                        # 本文件：文档索引
├── PROJECT_DOCUMENTATION.md         # 项目主文档
├── deployment/                      # 部署
│   ├── before_deploy.md
│   ├── docker.md
│   └── not_docker.md
├── backend/                         # 后端
│   ├── backend_design/              # 设计文档（含 Project、自动化示例）
│   └── backend_user_guide/          # 使用指南（含 Project 场景操作）
├── hardware/                        # 硬件与嵌入式
│   ├── hardware_code_design.md
│   └── hardware_guide.md
├── frontend/                        # 前端设计
│   └── frontend_design.md
├── mcp/                             # MCP 与 AI Agent
│   └── mcp_agent_guide.md
├── simulation/                      # 仿真（虚拟传感器/设备）
│   ├── simulation_guide.md
│   └── testing_guide.md
└── update_notes/                    # 版本更新日志
    ├── 0.10_update_notes.md
    ├── 0.9_update_notes.md
    ├── 0.8_update_notes.md
    ├── 0.7_update_notes.md
    ├── 0.6_update_notes.md
    ├── 0.5_update_notes.md
    ├── 0.4_update_notes.md
    ├── 0.3_update_notes.md
    └── 0.2_update_notes.md
```
