"""
WebSocket JWT 鉴权中间件。

由于浏览器 WebSocket 不支持自定义 Authorization header，
握手时只能通过 query string 带 token：ws://host/ws/...?token=<jwt>

约定的 close code：
    4001 = 未认证 / token 无效
    4003 = 权限不足（保留给后续按资源 ACL 用）
"""
from __future__ import annotations

import logging
from urllib.parse import parse_qsl, urlencode

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from django.db import InterfaceError, OperationalError

log = logging.getLogger(__name__)


class AuthenticationDependencyUnavailable(RuntimeError):
    """JWT 本身未判定无效，但用户数据库暂时不可用。"""


@database_sync_to_async
def _authenticate(token: str):
    """同步 JWT 校验。失败统一返回 AnonymousUser。"""
    if not token:
        return AnonymousUser()
    try:
        from rest_framework_simplejwt.authentication import JWTAuthentication
        from rest_framework_simplejwt.tokens import AccessToken

        payload = AccessToken(token)
        # 复用 SimpleJWT 的 HTTP 鉴权路径，确保 inactive 用户、用户不存在以及
        # 可选的密码撤销校验在 REST / WebSocket 两侧保持完全一致。
        return JWTAuthentication().get_user(payload)
    except (OperationalError, InterfaceError) as exc:
        raise AuthenticationDependencyUnavailable from exc
    except Exception as exc:
        log.debug("[ws-auth] token 校验失败: %s", exc)
        return AnonymousUser()


class JwtAuthMiddleware(BaseMiddleware):
    """解析 JWT，并在 Uvicorn 写握手日志前从原始 scope 移除 token。"""

    async def __call__(self, scope, receive, send):
        qs = (scope.get("query_string") or b"").decode("utf-8", errors="ignore")
        token = ""
        safe_pairs = []
        for key, value in parse_qsl(qs, keep_blank_values=True):
            if key.lower() == "token":
                if not token:
                    token = value
                continue
            safe_pairs.append((key, value))

        # Uvicorn 在 application 返回 accept/close 后，仍会从最初传入的同一个
        # scope 生成 "WebSocket <path>?<query>" 日志。必须在调用 BaseMiddleware
        # （它会复制 scope）之前就地清除凭据，同时保留其它非敏感查询参数。
        scope["query_string"] = urlencode(safe_pairs, doseq=True).encode("utf-8")
        try:
            scope["user"] = await _authenticate(token)
        except AuthenticationDependencyUnavailable as exc:
            # 依赖故障不是“token 无效”，不能让前端误删登录态或盲目 refresh。
            log.warning("[ws-auth] 用户数据库暂时不可用: %s", exc)
            scope["user"] = AnonymousUser()
            scope["auth_dependency_unavailable"] = True
        return await super().__call__(scope, receive, send)
