# SprIoT_Platform MCP

本目录是平台的 **AI 操作入口**。它实现标准 Model Context Protocol（MCP），让支持 MCP 的
Agent 在明确权限和安全边界内发现、读取、控制和管理 SprIoT_Platform 资源。

它不是 Django 插件，也不直接连接 MySQL、Redis 或 MQTT。所有业务操作统一经过 Django REST
API，因此继续复用平台已有的认证、序列化校验、管理员权限与命令发送服务。

## 安全边界

- 查询可使用名称模糊搜索；控制、更新、删除只接受精确 `sensor_id` / `device_id`。
- 命令执行和删除使用“预览 → 短时确认令牌 → 执行”两阶段流程。
- MCP 不提供任意 SQL、任意 API path 或任意 MQTT topic 发布工具。
- 默认 HTTP 只监听 `127.0.0.1`。Docker 端口同样只绑定宿主机回环地址。
- 平台账号、JWT、确认密钥只能通过环境变量注入，禁止写入仓库。
- 当前命令结果中的 `mqtt_published` 仅表示 broker 接受发布，不表示设备已执行。

> 当前 HTTP MCP 端点自身尚未接入 OAuth，不能直接通过 frpc 暴露到公网。公网部署前应实现
> MCP OAuth 2.1，或放在具有独立身份认证的可信反向代理后。不要把 Django JWT 原样透传为
> MCP 客户端凭据。

## 工具

| 工具 | 行为 |
|---|---|
| `search_iot_assets` | 搜索传感器和设备候选项 |
| `get_iot_asset` | 读取精确资源详情 |
| `query_iot_telemetry` | 读取传感器数据或设备状态历史 |
| `list_iot_commands` | 查看资源实际支持的命令和参数 |
| `preview_iot_command` | 校验命令并生成确认令牌 |
| `execute_iot_command` | 使用确认令牌发布命令 |
| `create_iot_asset` | 创建传感器或设备 |
| `update_iot_asset` | 更新资源元数据 |
| `preview_delete_iot_asset` | 预览删除影响并生成确认令牌 |
| `delete_iot_asset` | 永久删除资源 |

同时提供 `iot://catalog` 和 `iot://assets/{kind}/{asset_id}` 两类只读 MCP Resources。

## 本地 stdio 使用

```bash
cd mcp
python -m venv .venv
.venv/bin/pip install -e .

export IOT_API_BASE_URL=http://127.0.0.1:8000/api
export IOT_API_USERNAME=your_dedicated_staff_agent_account
export IOT_API_PASSWORD=your_password
export IOT_MCP_CONFIRMATION_SECRET='replace-with-a-long-random-secret'
.venv/bin/iot-mcp
```

`stdio` 是默认 transport，适合 Codex、Claude Desktop 等在本机直接拉起进程的客户端。也可以用
`IOT_API_ACCESS_TOKEN` 代替用户名密码，但短期 JWT 过期后无法自动重新签发，长期运行优先使用
独立 Agent 账号。当前平台写权限仍以 `is_staff` 判定，因此需要管理能力时应使用独立的非超级
管理员 staff 账号；细粒度 scope 将在后续阶段补齐。

## Docker Streamable HTTP

先在根目录 `.env` 配置：

```dotenv
IOT_API_USERNAME=your_dedicated_staff_agent_account
IOT_API_PASSWORD=your_password
IOT_MCP_CONFIRMATION_SECRET=replace-with-a-long-random-secret
MCP_HOST_PORT=48082
```

然后在项目根目录构建并启动完整服务：

```bash
docker compose build
docker compose up -d
```

`mcp` 已是默认 Compose 服务，不需要额外指定 profile。只想单独重建 MCP 时可以执行
`docker compose up -d --build mcp`。

本机 MCP 地址为 `http://127.0.0.1:48082/mcp`。可以使用 MCP Inspector 验证：

```bash
npx -y @modelcontextprotocol/inspector
```

## 测试

```bash
cd mcp
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

## 后续阶段

1. 后端增加 Agent/Service Account 与 `iot:read`、`iot:control`、`iot:manage` 等 scope。
2. 增加命令执行审计表、幂等键和基于 Redis/数据库的跨进程设备确认状态。
3. 为远程 Streamable HTTP 接入符合 MCP 规范的 OAuth 2.1。
4. 再逐步开放文件夹、项目、自动化规则与插件各自的 MCP 工具，不提供万能透传接口。
