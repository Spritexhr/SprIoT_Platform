FROM docker.m.daocloud.io/library/python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY iot_mcp ./iot_mcp

RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple .

ENV IOT_MCP_TRANSPORT=streamable-http \
    IOT_MCP_HOST=0.0.0.0 \
    IOT_MCP_PORT=8000

EXPOSE 8000

CMD ["iot-mcp"]
