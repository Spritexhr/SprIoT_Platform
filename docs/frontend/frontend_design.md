# SprIoT_Platform 前端设计

本文档概括当前 Vue 前端的主要结构和数据流。Project 场景的具体操作见 [Project 使用指南](../backend/backend_user_guide/project_guide.md)。

## 一、技术栈

| 类别 | 当前实现 |
|---|---|
| 框架 | Vue 3、Composition API、`<script setup>` |
| 构建 | Vite 5 |
| 路由 | Vue Router 4，页面懒加载与认证守卫 |
| 状态 | Pinia 3 |
| UI | Element Plus、Element Plus Icons |
| HTTP | Axios，JWT 注入与自动刷新 |
| 实时通信 | 原生 WebSocket + `useWebSocket` composable |
| 图表 | ECharts 6 |
| 流程图 | Vue Flow |
| 代码编辑 | CodeMirror 6 + Python 语法高亮 |
| 样式 | SCSS、CSS 变量、亮暗主题 |

浏览器不直接连接 MQTT。MQTT 消息由后端处理，前端通过 REST 获取静态/历史数据，通过 WebSocket 接收实时增量。

## 二、应用结构

```text
App.vue
└── RouterView
    ├── Login / Register
    └── AppLayout
        ├── AppSidebar
        ├── AppHeader
        └── AppMain → 业务页面
```

主布局采用可折叠侧边栏、顶栏和内容区；移动端切换为抽屉式导航。

```text
frontend/src/
├── api/             # Axios 实例及业务 API
├── assets/styles/   # 全局变量、主题和基础样式
├── components/      # 通用命令、状态等组件
├── composables/     # useWebSocket
├── layouts/         # 主框架布局
├── router/          # 路由与守卫
├── stores/          # app、user、locale、project
└── views/           # 各业务页面
```

## 三、路由与页面

| 路径 | 页面 | 说明 |
|---|---|---|
| `/login`、`/register` | 认证 | `guest: true` |
| `/` | 仪表盘 | 平台统计和运行概览 |
| `/sensors`、`/sensors/:sensorId` | 传感器 | 管理、最新值、历史、状态和命令 |
| `/devices`、`/devices/:deviceId` | 设备 | 管理、状态历史和控制命令 |
| `/automation`、`/automation/:id` | 自动化 | 自由脚本规则与 CodeMirror 编辑器 |
| `/projects` | 项目/场景 | 项目列表和统计 |
| `/projects/:id` | 项目工作台 | 房间导航与多视图实时监控 |
| `/projects/:id/config` | 项目配置 | 房间、成员和视图配置 |
| `/plugins` | 插件中心 | 已发现插件列表 |
| `/plugins/data_viz` | 数据可视化 | 独立时序查询插件 |
| `/settings` | 系统设置 | MQTT、平台配置和类型管理 |

业务页面继承父路由的 `requiresAuth`。路由守卫仅判断本地 Access Token 是否存在；真正的读写权限由后端 DRF 校验。

## 四、状态管理

| Store | 职责 |
|---|---|
| `app.js` | 侧边栏、抽屉、亮暗模式和配色主题 |
| `user.js` | Access/Refresh Token、用户资料和退出登录 |
| `locale.js` | 中英文文案及静态翻译函数 |
| `project.js` | Project layout、实时 samples、设备状态和告警统计 |

普通传感器/设备管理页主要使用页面内 REST 状态；跨组件持续更新的 Project 数据集中存入 Project Store：

```text
samples: Map<point_id, PointSample>
devices: Map<device_id, DeviceState>
layout:  Project sections + members
```

`findByBinding()` 同时支持 `sensor_id` 与 `sensor_id::data_key`。

## 五、Project 工作台

```text
ProjectWorkspace
├── 房间/工段导航
├── 连接状态、点位数、报警数
└── 当前房间视图
    ├── CardDashboard
    ├── DiagramView
    ├── TimeseriesView
    └── ControlSchemeView
```

- 卡片视图：实时数值、在线/报警筛选和设备命令。
- 流程图：Vue Flow 编辑/运行态，画布保存于 `ProjectView.config`。
- 时序视图：ECharts 展示传感器/设备历史与事件。
- 控制视图：管理双位、PI、PID 控制方案。

普通用户可查看；画布编辑、设备控制等多数写入口会结合 `userInfo.is_staff` 控制。项目配置路由本身仍可访问，因此后端 DRF 始终是最终权限边界。

## 六、HTTP 与认证

Axios 实例位于 `src/api/index.js`：

- `baseURL = /api`
- 自动附加 `Authorization: Bearer <access>`
- Access Token 距过期不足 30 秒时主动刷新
- 多个并发请求共享刷新任务
- 收到 401 时刷新并重放原请求
- Refresh 失败后清理 Token 并跳转登录

Token 保存键：

```text
iot-access-token
iot-refresh-token
```

主要 API 模块：

| 文件 | 领域 |
|---|---|
| `auth.js` | 登录、注册、资料和密码 |
| `sensors.js` | Sensor/SensorType、数据和命令 |
| `devices.js` | Device/DeviceType、状态和命令 |
| `automation.js` | 自由脚本规则 |
| `controlSchemes.js` | 双位、PI、PID 控制方案 |
| `projects.js` | Project、Section、成员、视图和时序 |
| `plugins.js` | 插件登记与启用状态 |
| `platformConfig.js` | 平台配置 |
| `system.js` | MQTT 状态和仪表盘统计 |

## 七、WebSocket

`useWebSocket.js` 提供统一实时连接：

- 自动把 Access Token 加到 `?token=` 查询串
- 25 秒 ping/pong 心跳
- 指数退避重连
- 关闭码 `4001` 时刷新 Token 后重连
- `onScopeDispose` 自动清理连接和定时器
- 按 `{event, data}` 分发到页面 handler

主要端点：

```text
/ws/sensors/             /ws/sensors/<id>/
/ws/devices/             /ws/devices/<id>/
/ws/automation/
/ws/system/mqtt/
/ws/projects/<project_id>/
/ws/plugins/<code>/
```

页面通常先通过 REST 获取快照，再用 WebSocket 接收增量，避免首次进入时等待下一条 MQTT 消息。

## 八、视觉与主题

平台默认采用暖米色、珊瑚橙的 Codex 风格，并支持亮暗模式和经典配色切换。颜色、圆角、阴影和间距集中在 CSS 变量中。

Project 工业场景可以使用工程蓝白图纸视觉，但仍复用平台布局、权限和数据组件。

## 九、开发与部署

本地验证：

```bash
cd /Users/xhr_mac/server/iot_control_platform/frontend
npm run dev
npm run build
```

生产前端由 nginx 提供构建产物，不是 Vite dev server。源码修改后必须重建：

```bash
cd /Users/xhr_mac/server/iot_control_platform
docker compose build frontend
docker compose up -d frontend
```

验证实际产物：

```bash
docker exec iot-frontend sh -c "grep -o 'index-[A-Za-z0-9_-]*\.js' /usr/share/nginx/html/index.html"
```

## 十、维护约定

- 新 API 统一放入 `src/api/`，调用路径不重复写 `/api`。
- 持续实时数据优先复用 `useWebSocket`。
- Project 视图必须按 Section 限定候选成员。
- 写操作入口结合 `is_staff` 隐藏，但不能替代后端权限。
- 新 P&ID 普通图元优先加入 `projects/diagram/editor/symbols.js` 注册表。
- 不在前端写入演示或生产种子数据。

---

*文档更新日期：2026 年 6 月*
