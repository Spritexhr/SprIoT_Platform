# SprIoT_Platform - 前端

基于 Vue 3 + Vite 构建的 SprIoT_Platform 前端，实现前后端分离架构。

## 技术栈

| 类别     | 技术                |
|----------|---------------------|
| 框架     | Vue 3 (Composition API) |
| 构建工具 | Vite               |
| 路由     | Vue Router 4       |
| 状态管理 | Pinia              |
| UI 组件  | Element Plus       |
| HTTP 请求| Axios              |
| 样式     | SCSS               |

## 开发

### 环境要求

- Node.js 18+
- npm 或 pnpm

### 安装依赖

```bash
npm install
```

### 启动开发服务器

```bash
npm run dev
```

开发服务器将在 `http://localhost:5173` 启动。`/api` 请求会自动代理到 `http://127.0.0.1:8000`（需确保 Django 后端已启动）。

### 构建生产版本

```bash
npm run build
```

构建产物将输出到 `dist` 目录。

### 预览生产构建

```bash
npm run preview
```

## 项目结构

```
frontend/
├── src/
│   ├── api/              # API 接口封装
│   │   ├── index.js      # Axios 实例、拦截器
│   │   ├── auth.js       # 认证相关
│   │   ├── sensors.js    # 传感器
│   │   ├── devices.js    # 设备
│   │   ├── automation.js # 自动化规则
│   │   └── system.js     # 系统接口（MQTT、仪表盘等）
│   ├── assets/           # 静态资源
│   │   └── styles/       # 全局样式 (SCSS)
│   ├── components/       # 公共组件
│   │   ├── common/      # 通用组件（如 CommandPanel）
│   │   ├── devices/     # 设备相关
│   │   └── sensors/     # 传感器相关
│   ├── layouts/         # 布局组件
│   │   ├── AppLayout.vue
│   │   ├── AppHeader.vue
│   │   ├── AppSidebar.vue
│   │   └── AppMain.vue
│   ├── router/          # 路由配置
│   ├── stores/          # Pinia 状态
│   ├── utils/           # 工具函数
│   ├── views/           # 页面视图
│   │   ├── auth/        # 登录、注册
│   │   ├── dashboard/   # 仪表盘
│   │   ├── sensors/    # 传感器管理
│   │   ├── devices/    # 设备管理
│   │   ├── automation/ # 自动化规则
│   │   └── settings/   # 系统设置
│   ├── App.vue
│   └── main.js
├── index.html
├── vite.config.js
├── package.json
├── Dockerfile            # 容器构建
└── nginx.conf            # Nginx 配置（生产部署）
```

## 后端联动

开发时，请确保 Django 后端已启动（默认 `http://127.0.0.1:8000`）。Vite 会将 `/api` 请求代理到后端。

生产部署时，通常由 Nginx 将 `/api` 代理到后端服务（见 `nginx.conf`）。

## 部署

| 方式 | 说明 |
|-----|------|
| Docker | 项目根目录 `docker compose up -d --build`，详见 [docs/deployment/docker.md](../docs/deployment/docker.md) |
| 非 Docker | 构建后由 Nginx 提供静态文件并代理 `/api`，详见 [docs/deployment/not_docker.md](../docs/deployment/not_docker.md) |

## 相关文档

- [前端设计说明](../docs/frontend/frontend_design.md)
- [项目文档中心](../docs/README.md)
