# 物联网控制平台 Docker 部署指南

本文档详细介绍如何使用 Docker 容器化部署物联网控制平台的 Django 后端和 Vue 前端的完整流程。即使你不熟悉容器技术，按照步骤也能完成部署。

---

## 一、前置知识：什么是 Docker？

### 1.1 容器与 Docker 简介

**Docker** 是一种容器技术，可以简单理解为：
- **镜像（Image）**：类似"安装包"，包含应用及其运行所需的一切（代码、依赖、环境）
- **容器（Container）**：镜像运行后的实例，类似一个轻量级、隔离的"小虚拟机"
- **Docker Compose**：用一个配置文件同时启动多个容器，并管理它们之间的网络和数据卷

### 1.2 为什么用 Docker 部署？

| 传统部署                     | Docker 部署                            |
|----------------------------|----------------------------------------|
| 需在服务器上配置 Python、Node、数据库等 | 环境全部封装在镜像中                    |
| 不同项目依赖可能冲突           | 每个项目有独立环境，互不影响              |
| 换一台机器要重新配置一遍         | 一条命令即可运行，可移植性强              |

---

## 二、环境准备

### 2.1 安装 Docker

1. **Windows**
   - 下载 [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)
   - 安装后重启，确保 WSL 2 已启用
   - 打开 Docker Desktop，等待其完全启动（托盘图标无黄色）

2. **验证安装**
   ```bash
   docker --version
   docker compose version
   ```
   两条命令都能正常输出版本号即表示安装成功。

### 2.2 项目结构与 Docker 相关文件

部署完成后，项目根目录应包含以下结构：

```
iot_control_platform/           # 项目根目录
├── docker-compose.yml          # 编排文件：一次启动所有服务
├── .env.example                # 环境变量示例（复制为 .env 后修改）
├── docs/
│   ├── deployment/
│   │   └── docker.md           # 本部署文档
├── iot_control_platform/       # Django 后端
│   ├── Dockerfile              # 后端镜像构建说明
│   └── ...
└── frontend/                   # Vue 前端
    ├── Dockerfile              # 前端镜像构建说明
    ├── nginx.conf              # Nginx 配置（代理 API 到后端）
    └── ...
```

---

## 三、部署架构说明

### 3.1 整体架构图

```
                    ┌─────────────────────────────────────┐
                    │           用户浏览器                    │
                    │   http://localhost:48080（默认）      │
                    └──────────────────┬──────────────────┘
                                       │
                                       ▼
                    ┌─────────────────────────────────────┐
                    │     Nginx 容器 (端口 80)              │
                    │  - 提供前端静态文件（Vue 构建产物）        │
                    │  - 将 /api/* 请求代理到 Django 后端      │
                    └──────────────┬──────────────┬────────┘
                                   │              │
                     前端静态资源   │              │  /api/*
                                   ▼              ▼
                    ┌──────────────────┐  ┌──────────────────────┐
                    │  Frontend 容器    │  │  Django 后端容器       │
                    │  (Vue 构建文件)   │  │  Gunicorn + Django     │
                    └──────────────────┘  └──────────┬───────────┘
                                                     │
                                    ┌────────────────┬────────────────┐
                                    ▼                ▼                ▼
                         ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
                         │  MySQL 8.0   │  │  公网 EMQX    │  │  数据卷       │
                         │  (数据库)     │  │  (MQTT 消息)  │  │  (持久化)     │
                         └──────────────┘  └──────────────┘  └──────────────┘
```

### 3.2 服务说明

| 服务        | 说明                         | 对外端口 |
|-------------|------------------------------|----------|
| `mysql`     | MySQL 8.0 数据库             | 3307（可配置） |
| `frontend`  | Vue 前端构建 + Nginx 提供静态文件与 API 代理 | 48080（可由 `.env:FRONTEND_PORT` 配置） |
| `backend`   | Django REST API + Gunicorn + MQTT 客户端（连接 EMQX） | 8000 → 48081（Admin 直连，可由 `.env:ADMIN_PORT` 配置） |
| 数据卷       | 持久化 MySQL 数据            | -        |

---

## 四、一步步部署

### 4.1 创建环境变量文件

在项目根目录（与 `docker-compose.yml` 同级）创建 `.env` 文件：

```bash
cd /path/to/iot_control_platform   # 进入项目根目录
cp .env.example .env               # Linux/Mac
# 或 copy .env.example .env        # Windows
```

然后编辑 `.env`，**务必修改 `DB_PASSWORD`** 和 `SECRET_KEY`：

```env
# Django 必备
SECRET_KEY=your-secret-key-change-in-production
DEBUG=True
ALLOWED_HOSTS=*

# MySQL 数据库
DB_NAME=iot_platform
DB_USER=root
DB_PASSWORD=你的MySQL密码

# 端口
MYSQL_HOST_PORT=3307
FRONTEND_PORT=48080
ADMIN_PORT=48081
```

> **注意**：`.env` 不再包含 MQTT 相关配置。MQTT broker、端口、用户名、密码等已迁移到 `PlatformConfig` 表，由 `configure` 命令在部署后维护（见 4.4）。
>
> 生产环境务必使用强密码，并修改 `SECRET_KEY`、`DEBUG=False`、`ALLOWED_HOSTS=yourdomain.com`。

如果 Django Admin 通过 HTTPS 反向代理域名访问，还需要在 `.env` 中配置完整 Origin（包含协议）：

```dotenv
EXTRA_CSRF_TRUSTED_ORIGINS=https://iot-admin.example.com
```

多个域名使用逗号分隔。该配置只用于 Django CSRF Origin 校验；不要为了绕过 403 而关闭 CSRF 中间件。

### 4.2 构建并启动所有服务

在项目根目录执行：

```bash
docker compose up -d --build
```

参数说明：
- `up`：启动服务
- `-d`：后台运行（不占用当前终端）
- `--build`：启动前先构建镜像（首次或代码变更后需要）

**首次运行**可能需要几分钟，因为要下载基础镜像和安装依赖。

### 4.3 初始化数据库（首次部署必做）

后端启动时会自动执行：
1. **数据库迁移**：`migrate --noinput`
2. **平台配置种子**：`configure --init`（将 `defaults.py` 中的默认值写入 DB，仅补缺失的 key）

首次部署需要做两件事：

**(1) 创建管理员账号**

```bash
docker compose exec backend python manage.py createsuperuser
```

**(2) 配置 MQTT 等业务参数**（启动后必做）

默认 MQTT broker 是 `127.0.0.1:1883`，仅作占位。运行交互式 wizard 改为实际值：

```bash
docker compose exec backend python manage.py configure
```

```
=== 平台配置 wizard ===
直接回车保留当前值；按 Ctrl+C 中止。

[mqtt]
  mqtt_broker (MQTT/EMQX 服务器地址) [127.0.0.1]: 192.168.1.10
  mqtt_port (MQTT 端口) [1883]: 1883
  mqtt_username (...) []: emqx_user
  mqtt_password (...) []: ********
...

触发 reload:
  MQTT: reconnected
```

写完自动调 reload，MQTT 服务无感重连，**不需要重启容器**。配置错了再跑一次 wizard 即可。

也可以单键设置：

```bash
docker compose exec backend python manage.py configure \
    --set mqtt_broker=192.168.1.10 --set mqtt_port=1883
```

完整使用见 [平台配置使用指南](../backend/backend_user_guide/platform_settings_guide.md)。

### 4.4 访问应用

- **前端页面**：http://localhost:48080（`.env` 中 `FRONTEND_PORT=48080`）
- **Django Admin**：http://localhost:48081/admin/（`.env` 中 `ADMIN_PORT=48081`，直连后端）

使用上一步创建的超级管理员账号登录 Admin。

---

## 五、常用 Docker 命令

### 5.1 查看运行状态

```bash
# 查看所有容器状态
docker compose ps

# 查看后端日志（排查错误时有用）
docker compose logs backend -f

# 查看前端日志
docker compose logs frontend -f
```

### 5.2 停止与重启

```bash
# 停止所有服务
docker compose down

# 停止并删除数据卷（慎用！会清空数据库）
docker compose down -v

# 重启单个服务
docker compose restart backend
```

### 5.3 更新代码后重新部署

```bash
# 重新构建并启动
docker compose up -d --build

# 若仅修改了 Python 代码，可只重启后端
docker compose up -d --build backend
```

### 5.4 进入容器调试

```bash
# 进入后端容器 bash
docker compose exec backend bash

# 进入后可在容器内执行 Django 命令
python manage.py shell
python manage.py makemigrations
```

---

## 六、配置文件详解

### 6.1 后端 Dockerfile（`iot_control_platform/Dockerfile`）

```dockerfile
# 构建阶段：安装依赖
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .

# 启动命令
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
```

- `python:3.12-slim`：轻量 Python 基础镜像
- `WORKDIR /app`：工作目录
- `gunicorn`：生产级 WSGI 服务器，替代 `runserver`

### 6.2 前端 Dockerfile（`frontend/Dockerfile`）

采用**多阶段构建**：
1. 第一阶段：用 Node 镜像执行 `npm run build` 生成静态文件
2. 第二阶段：用 Nginx 镜像，只保留 `dist/` 和 Nginx 配置

这样最终镜像体积小，且不包含 Node 和源码。

### 6.3 Nginx 配置（`frontend/nginx.conf`）

关键点：
- 根路径 `/` 提供前端静态文件
- `/api/` 代理到后端容器 `http://backend:8000`
- 支持 Vue Router 的 history 模式（刷新不 404）

### 6.4 docker-compose.yml

- `mysql`：MySQL 8.0 数据库，数据持久化到 `mysql_data` 卷
- `backend`：Django + Gunicorn，启动时执行 `migrate` 和 `configure --init`（仅补缺失的默认配置），通过 `PlatformConfig` 表读取 MQTT 配置
- `frontend`：Vue 构建 + Nginx，代理 `/api/` 到 backend，依赖 `backend`
- `volumes`：`mysql_data` 持久化 MySQL 数据

---

## 七、生产环境注意事项

### 7.1 安全配置

1. **SECRET_KEY**：必须改为随机字符串，不要使用文档中的示例
2. **DEBUG**：务必设为 `False`
3. **ALLOWED_HOSTS**：填写实际域名或 IP，如 `yourdomain.com,www.yourdomain.com`

### 7.2 数据库说明

Docker 部署默认使用 **MySQL 8.0**，通过 `DB_USE_MYSQL=true`、`DB_HOST=mysql` 等环境变量配置。数据持久化在 `mysql_data` 卷中。

### 7.3 使用 HTTPS

生产环境建议在 Nginx 前再加一层反向代理（如 Traefik、Nginx Proxy Manager），或直接在 Nginx 配置中启用 SSL。

---

## 八、常见疑问解答

### Q：为什么 Docker 里是全新的数据库？之前本地开发的数据呢？

**原因**：本地开发和 Docker 用的是**两个完全独立的数据库**：

| 运行方式 | 数据库 |
|---------|--------|
| 本地开发 (`python manage.py runserver`) | 默认 SQLite：`iot_control_platform/db.sqlite3`，或 MySQL（若配置 `DB_USE_MYSQL=true`） |
| Docker 运行 | MySQL 容器，数据卷 `mysql_data` |

Docker 使用 MySQL 容器，不会读取本地 SQLite 或本地 MySQL 数据，所以看起来是"全新的"。

**如何让 Docker 使用已有 MySQL 数据？** 可将 `mysql_data` 卷指向宿主机目录，或从本地备份导入。

### Q：环境变量（SECRET_KEY、DEBUG、ALLOWED_HOSTS）是如何传给后端的？

流程如下：

1. **`.env` 文件**：在项目根目录，内容示例：
   ```
   SECRET_KEY=your-secret-key
   DEBUG=True
   ALLOWED_HOSTS=localhost,127.0.0.1
   ```

2. **docker-compose.yml**：backend 服务中有 `env_file: - .env`，Docker Compose 会读取 `.env` 并将其中变量**注入到容器的环境变量**中。

3. **Django settings.py**：已修改为从环境变量读取（若存在则优先使用）：
   ```python
   SECRET_KEY = os.environ.get("SECRET_KEY", "默认值")
   DEBUG = os.environ.get("DEBUG", "True").lower() in ("true", "1", "yes")
   ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "*").split(",")]
   ```

因此，修改 `.env` 后重启容器即可生效：`docker compose restart backend`

---

## 九、常见问题排查

### Q1：访问前端出现 502 Bad Gateway

- 检查后端是否正常：`docker compose logs backend`
- 确认 `backend` 容器名称在 Nginx 配置中与 `docker-compose.yml` 的 service 名一致（通常为 `backend`）

### Q2：登录后接口 401 或 CORS 错误

- 确认前端访问地址与 Django 的 `CORS_ALLOWED_ORIGINS`、`CSRF_TRUSTED_ORIGINS` 一致
- 若通过域名访问，需在 `settings.py` 中加入该域名

### Q3：数据库迁移失败

```bash
docker compose exec backend python manage.py migrate
```

若报错，可尝试：

```bash
docker compose exec backend python manage.py makemigrations
docker compose exec backend python manage.py migrate
```

### Q4：Docker 部署后无法连接 EMQX/MQTT

**说明**：MQTT 配置已不在 `.env`，存于 `PlatformConfig` 表。默认占位值是 `127.0.0.1:1883`，连不上是预期行为。

修改方式：

```bash
# 公网 EMQX
docker compose exec backend python manage.py configure \
    --set mqtt_broker=实际IP --set mqtt_port=1883

# EMQX 在宿主机运行（Docker Desktop 的 Windows/Mac）
docker compose exec backend python manage.py configure \
    --set mqtt_broker=host.docker.internal --set mqtt_port=1883

# EMQX 开启认证
docker compose exec backend python manage.py configure \
    --set mqtt_username=user --set mqtt_password=pass
```

写完会自动 reload，MQTT 服务无感重连，**不需要重启容器**。

### Q5：端口被占用

修改 `.env` 中的 `FRONTEND_PORT`，例如改为 `28080`，然后访问 http://localhost:28080。

---

## 十、快速命令速查表

| 操作           | 命令                                          |
|----------------|-----------------------------------------------|
| 首次启动       | `docker compose up -d --build`                |
| 首次数据库迁移 | `docker compose exec backend python manage.py migrate` |
| 创建管理员     | `docker compose exec backend python manage.py createsuperuser` |
| 配置平台（wizard） | `docker compose exec backend python manage.py configure` |
| 单键设置配置   | `docker compose exec backend python manage.py configure --set mqtt_broker=x.x.x.x` |
| 列出当前配置   | `docker compose exec backend python manage.py configure --list` |
| 查看状态       | `docker compose ps`                           |
| 查看后端日志   | `docker compose logs backend -f`              |
| 停止服务       | `docker compose down`                         |
| 重新构建并启动 | `docker compose up -d --build`                 |

---

## 十一、总结

完成以上步骤后，你的物联网控制平台将运行在 Docker 容器中，具备以下特点：

1. **环境一致**：开发、测试、生产使用相同镜像，减少"在我电脑上能跑"类问题  
2. **易于迁移**：换服务器只需安装 Docker 和复制项目文件  
3. **隔离性好**：各服务在独立容器中，互不干扰  
4. **便于扩展**：未来可在此基础上增加 Redis、MySQL、监控等组件  

如遇到本文未覆盖的问题，可结合 `docker compose logs` 和各服务的官方文档进行排查。
