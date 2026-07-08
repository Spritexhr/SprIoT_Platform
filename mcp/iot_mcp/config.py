"""MCP 服务运行配置，只从环境变量读取敏感信息。"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    api_base_url: str
    api_username: str | None
    api_password: str | None
    api_access_token: str | None
    confirmation_secret: str
    confirmation_ttl_seconds: int
    request_timeout_seconds: int
    host: str
    port: int
    transport: str

    @classmethod
    def from_env(cls) -> "Settings":
        transport = os.getenv("IOT_MCP_TRANSPORT", "stdio").strip().lower()
        if transport not in {"stdio", "streamable-http"}:
            raise ValueError("IOT_MCP_TRANSPORT 只能是 stdio 或 streamable-http")
        secret = os.getenv("IOT_MCP_CONFIRMATION_SECRET") or ""
        if transport == "streamable-http" and not secret:
            raise ValueError(
                "Streamable HTTP 模式必须设置 IOT_MCP_CONFIRMATION_SECRET"
            )
        if not secret:
            # stdio 是单进程本地会话，允许使用会话级随机密钥。
            secret = secrets.token_urlsafe(32)
        return cls(
            api_base_url=os.getenv(
                "IOT_API_BASE_URL", "http://127.0.0.1:8000/api"
            ).rstrip("/"),
            api_username=os.getenv("IOT_API_USERNAME") or None,
            api_password=os.getenv("IOT_API_PASSWORD") or None,
            api_access_token=os.getenv("IOT_API_ACCESS_TOKEN") or None,
            confirmation_secret=secret,
            confirmation_ttl_seconds=_env_int("IOT_MCP_CONFIRMATION_TTL", 120),
            request_timeout_seconds=_env_int("IOT_MCP_REQUEST_TIMEOUT", 15),
            host=os.getenv("IOT_MCP_HOST", "127.0.0.1"),
            port=_env_int("IOT_MCP_PORT", 8000),
            transport=transport,
        )

    def validate_api_credentials(self) -> None:
        if self.api_access_token:
            return
        if self.api_username and self.api_password:
            return
        raise ValueError(
            "未配置平台凭据：请设置 IOT_API_ACCESS_TOKEN，或同时设置 "
            "IOT_API_USERNAME/IOT_API_PASSWORD"
        )
