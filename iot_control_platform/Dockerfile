# 物联网控制平台 - Django 后端镜像
# 使用 Python 3.12 轻量级镜像
FROM docker.m.daocloud.io/library/python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖（mysqlclient 需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装 Python 包（使用国内镜像源）
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 复制项目代码
COPY . .

# 暴露端口（Gunicorn 默认监听 8000）
EXPOSE 8000

# 使用 Gunicorn + UvicornWorker 启动 Django ASGI 应用：
# 同时支持 HTTP（DRF 同步 view 由 worker 自动 sync_to_async 包装）和 WebSocket（channels）。
# 注意：docker-compose.yml 的 command 会覆盖此 CMD；改这里仅为镜像直跑场景兜底。
CMD ["gunicorn", "config.asgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--worker-class", "uvicorn.workers.UvicornWorker"]
