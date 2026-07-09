"""面向 Django REST API 的异步客户端。MCP 服务不直接访问 ORM 或数据库。"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import Settings


class IoTAPIError(RuntimeError):
    """保留 HTTP 状态和可重试语义的平台 API 错误。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "api_error",
        status_code: int | None = None,
        retryable: bool = False,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.details = details


class IoTAPIClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._access_token = settings.api_access_token
        self._refresh_token: str | None = None
        self._auth_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            base_url=f"{settings.api_base_url}/",
            timeout=settings.request_timeout_seconds,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _login(self, *, force: bool = False) -> None:
        if self._access_token and not force:
            return
        async with self._auth_lock:
            if self._access_token and not force:
                return
            self.settings.validate_api_credentials()
            if self.settings.api_access_token:
                self._access_token = self.settings.api_access_token
                return
            response = await self._http.post(
                "auth/login/",
                json={
                    "username": self.settings.api_username,
                    "password": self.settings.api_password,
                },
            )
            if response.status_code >= 400:
                raise IoTAPIError(
                    "平台身份认证失败",
                    code="authentication_failed",
                    status_code=response.status_code,
                    details=_response_data(response),
                )
            data = response.json()
            self._access_token = data.get("access")
            self._refresh_token = data.get("refresh")
            if not self._access_token:
                raise IoTAPIError("登录响应缺少 access token", code="invalid_auth_response")

    async def _refresh_or_login(self) -> None:
        if self._refresh_token:
            response = await self._http.post(
                "auth/refresh/", json={"refresh": self._refresh_token}
            )
            if response.status_code < 400:
                self._access_token = response.json().get("access")
                if self._access_token:
                    return
        self._access_token = None
        await self._login(force=True)

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        await self._login()
        for attempt in range(2):
            try:
                response = await self._http.request(
                    method,
                    endpoint.lstrip("/"),
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {self._access_token}"},
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise IoTAPIError(
                    "无法连接 SprIoT_Platform API",
                    code="platform_unavailable",
                    retryable=True,
                    details=str(exc),
                ) from exc
            if response.status_code == 401 and attempt == 0:
                await self._refresh_or_login()
                continue
            if response.status_code >= 400:
                data = _response_data(response)
                message = data.get("detail") if isinstance(data, dict) else None
                raise IoTAPIError(
                    message or f"平台 API 返回 HTTP {response.status_code}",
                    status_code=response.status_code,
                    code=_status_code_name(response.status_code),
                    retryable=response.status_code >= 500,
                    details=data,
                )
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        raise IoTAPIError("身份认证失败", code="authentication_failed", status_code=401)

    async def get(self, endpoint: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", endpoint, params=params)

    async def post(self, endpoint: str, *, data: dict[str, Any]) -> Any:
        return await self.request("POST", endpoint, json=data)

    async def patch(self, endpoint: str, *, data: dict[str, Any]) -> Any:
        return await self.request("PATCH", endpoint, json=data)

    async def delete(self, endpoint: str) -> None:
        await self.request("DELETE", endpoint)


def unpack_results(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return payload["results"]
    if isinstance(payload, list):
        return payload
    raise IoTAPIError("平台列表响应格式无效", code="invalid_api_response", details=payload)


def _response_data(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:1000]


def _status_code_name(status_code: int) -> str:
    return {
        400: "invalid_request",
        401: "authentication_failed",
        403: "permission_denied",
        404: "not_found",
        409: "conflict",
        429: "rate_limited",
    }.get(status_code, "api_error")
