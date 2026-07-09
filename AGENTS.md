# SprIoT_Platform — Codex 协作说明

## 环境

| 项 | 值 |
|---|---|
| Python | `/Users/xhr_mac/miniconda3/envs/iot_platform_env/bin/python` (conda env `iot_platform_env`) |
| Django | 6.0.3，DRF |
| 数据库 | SQLite（`iot_control_platform/db.sqlite3`） |
| 后端工作目录 | `/Users/xhr_mac/server/iot_control_platform/iot_control_platform/` |
| 前端工作目录 | `/Users/xhr_mac/server/iot_control_platform/frontend/` |
| 前端栈 | Vue 3 + Vite + Pinia + Vue Router + Element Plus + Vue Flow |
| Django settings | `config/settings.py` |
| Django urls | `config/urls.py` |
| 主干分支 | `main`；开发分支 `beta_version` |

> ⚠️ 系统 `python` 不带 Django。所有后端命令必须用绝对路径 `/Users/xhr_mac/miniconda3/envs/iot_platform_env/bin/python` 或先 `conda activate iot_platform_env`。
>
> ⚠️ **实际运行在 Docker（见下方「Docker 部署」），上表的 conda/SQLite 仅用于本地脱机开发。** 查/改运行中的数据、调试线上行为，一律走容器，不要查本地 `db.sqlite3`（本地库通常是空的，会误导）。

## Docker 部署（实际运行环境，以此为准）

通过 `docker-compose.yml` 编排，对外经 `frpc` 暴露在 `iot.gxmzucodeclub.top`。

| 容器 | 镜像/角色 |
|---|---|
| `iot-backend` | Django ASGI（仅发布模式 MQTT，`IOT_MQTT_RUNNER=false`） |
| `iot-mqtt-runner` | 独立 MQTT 完整模式进程（订阅+落库，`mqtt_runner` 命令） |
| `iot-frontend` | **nginx 跑构建产物**（Dockerfile 多阶段 `npm build` → `/usr/share/nginx/html`），**非 vite dev/HMR** |
| `iot-mysql` | **数据库 = MySQL**（不是本地 SQLite） |
| `iot-redis` | channels 层 / 实时广播 |
| `frpc` | 内网穿透，对外访问入口 |

```bash
# 查/改运行中的数据（务必走 backend 容器，连的是 MySQL）
docker exec iot-backend python manage.py shell -c "..."
docker exec iot-backend python manage.py migrate

# 改前端源码后必须重建镜像才生效（nginx 服务的是构建产物，不会热更新）
cd /Users/xhr_mac/server/iot_control_platform
docker compose build frontend && docker compose up -d frontend
# 验证上线：hash 变化即新产物
docker exec iot-frontend sh -c "grep -o 'index-[A-Za-z0-9_-]*\.js' /usr/share/nginx/html/index.html"

docker ps                       # 看容器状态
docker logs iot-backend --tail 50
```

> 改前端可先在本地 `frontend/` 跑 `npm run build` 快速验证编译（本地有 node_modules），通过后再 docker 重建部署。

## 本地脱机开发命令（不连 Docker 时）

```bash
# 后端（在 iot_control_platform/iot_control_platform/ 目录）
/Users/xhr_mac/miniconda3/envs/iot_platform_env/bin/python manage.py check
/Users/xhr_mac/miniconda3/envs/iot_platform_env/bin/python manage.py makemigrations
/Users/xhr_mac/miniconda3/envs/iot_platform_env/bin/python manage.py migrate
/Users/xhr_mac/miniconda3/envs/iot_platform_env/bin/python manage.py runserver

# 前端（在 frontend/ 目录）
npm run dev
npm run build
```

## 后端架构

### 主 Django apps
- `sensors`、`devices`、`automation`、`platform_settings`
- 主模型：`sensors.Sensor` / `sensors.SensorData` / `sensors.SensorStatusCollection`、`devices.Device` / `devices.DeviceStatusCollection`、对应的 `SensorType` / `DeviceType`

### 插件机制（重要）
- 插件目录：`iot_control_platform/plugins/<name>/`，必须含 `plugin.json`（`enabled: true` 才生效）
- `plugins/__init__.py` 的 `discover_plugins()` 自动发现并加入 `INSTALLED_APPS`
- `config/urls.py` 自动挂载到 `/api/plugins/<name>/`
- 插件 app 命名约定：`apps.py` 中 `name="plugins.<name>"`，`label="plugin_<name>"`

现有插件：`eb_plant`（乙苯装置大屏）、`plant_diagram`（P&ID 画板编辑器）、`data_viz`（时序可视化）

### 解耦规则（2026-05-15 已实施，重要约束）
**主模型 `Sensor` / `Device` 必须保持插件清洁**——任何插件不得：
1. 给主模型加字段（曾有 `plant_code` / `plant_metadata`，已彻底移除）
2. 写 management 命令往主模型 seed 数据
3. 在 `services/` 通用层引用具体插件名

正确模式（以 `eb_plant` 为参考）：
- 插件在 `plugins/<name>/models.py` 定义自己的表
- 通过 `OneToOneField("sensors.Sensor", on_delete=CASCADE)` 引用主模型；主模型对插件零感知
- 通过 Django signals（如 `post_save` of `SensorData`）在 `plugins/<name>/signals.py` 注入实时数据流；`apps.py` 的 `ready()` 中 `from . import signals`
- 插件提供"从主模型导入到自有表"的 API（参考 `plugins/eb_plant/views.py` 的 `bindable_sources` + `sensor_bindings` CRUD）

### 实时数据链路（0.8 起：WebSocket + Redis）

```
MQTT broker
  → iot-mqtt-runner 容器（独立进程，sensors/management/commands/mqtt_runner.py）
    paho 后台线程 → services/sensors_service/* / devices_service/* 落库
  → 各模型 post_save 信号
    主层 services/realtime/signals.py（SensorData / SensorStatusCollection /
                                       DeviceStatusCollection / AutomationRule）
    插件层 plugins/<name>/signals.py（按 binding 过滤，例如 EB）
  → transaction.on_commit(...) → services/realtime/dispatch.publish_*
  → async_to_sync(channel_layer.group_send) → Redis（channels_redis）
  → backend 容器的 ASGI worker → consumer.broadcast_*
  → 前端 useWebSocket（/ws/...）
```

**Group 命名规则**：按"资源 × 资源ID"，不按"插件 × 事件"。
- `sensors.{sensor_id}` / `sensors.all`
- `devices.{device_id}` / `devices.all`
- `automation.rules` / `system.mqtt` / `plugins.{plugin_code}`

**MQTT 客户端的两种模式**（`sensors/apps.py:_is_publisher_only`）：
- **完整模式**（`mqtt_runner` 命令或本地开发单进程）：连接 + 订阅 + 注册 handler + 绑定 publisher
- **仅发布模式**（docker 的 backend worker，`IOT_MQTT_RUNNER=false`）：连接 + 绑定 publisher，**不订阅**——避免多 worker 重复处理消息，但保留命令下发能力

**WS 端点列表**（`config/asgi.py`）：
- `/ws/_ping/` 联调
- `/ws/sensors/` `/ws/sensors/<id>/`
- `/ws/devices/` `/ws/devices/<id>/`
- `/ws/automation/`
- `/ws/system/mqtt/`
- `/ws/plugins/<code>/`（插件 plugin.json 加 `"ws_module"` 字段动态挂载，参考 eb_plant/routing.py）

**WS 握手鉴权**：`?token=<jwt>` 查询串（浏览器 WebSocket 不支持自定义 header），`services/realtime/middleware.py` 解析；4001 = token 无效，前端 useWebSocket 收到后会触发一次 refresh 重连。

**PointSample**（仅插件层用）字段名 `plugin_code`（非 `plant_code`），`ingest_sensor_data` 入参用 `binding` 鸭子类型对象（需有 `tag/unit/data_key/hi_threshold/lo_threshold/severity` 属性）。

## 前端架构

- 入口路由：`frontend/src/router/index.js`，`requiresAuth` meta 配合 `localStorage['iot-access-token']` 做守卫；`guest: true` 的页面为登录/注册
- axios 实例：`frontend/src/api/index.js`，baseURL 含 `/api` 前缀；调用时路径以 `/` 开头不含 `/api`
- Pinia stores：`frontend/src/stores/`，命名风格 `useXxxStore`
- 插件页面：`frontend/src/views/plugins/<name>/`
- 插件 API：`frontend/src/api/plugins/<name>.js`
- WebSocket：`frontend/src/composables/useWebSocket.js`，与原 useSSE API 兼容（`status / displayStatus / start / stop`），多一个 `send()`。内置：自动重连指数退避、25s 心跳 ping/pong、token 自动附 query string、4001 close → refresh token → 重连

## 权限模型
- DRF token 认证 + session 兼容
- 写接口默认 `IsAuthenticated + IsAdminUser`（即 `is_staff=True`）
- 读接口默认 `IsAuthenticated`
- WebSocket 握手用 query string `?token=<jwt>` 鉴权（浏览器 WS 不支持自定义 header），后端 `JwtAuthMiddleware` 校验 SimpleJWT AccessToken

## 协作惯例
- 用户偏好中文交流、中文代码注释
- 用户偏好简洁清晰的架构；明确反感"主模型被插件污染"
- 修改架构性问题时，倾向于**一次性彻底解耦**而不是渐进兼容（参考本次 EB 解耦的"一并改造 plant_diagram + 删模拟器 + 清数据"策略）
- 前端 UI 风格："Codex 官网风格（暖米色 + 珊瑚橙）"，EB 大屏单独走"工程蓝白图纸风格"
- 演示/模拟数据不要进生产代码——所有测试数据由用户手动创建

## 已知文档
- `docs/EB装置大屏使用手册.md` — 含 2026-05-15 重构提示，旧流程段落保留作历史参考
- `docs/development_plans/` — 大功能方案文档目录
