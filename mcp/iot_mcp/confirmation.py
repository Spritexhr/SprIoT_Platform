"""为危险操作生成短时、防篡改、单次使用的确认令牌。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any


class ConfirmationError(ValueError):
    pass


class ConfirmationStore:
    def __init__(self, secret: str, ttl_seconds: int = 120) -> None:
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = ttl_seconds
        self._used_nonces: dict[str, int] = {}
        self._lock = threading.Lock()

    def issue(self, action: str, payload: dict[str, Any]) -> str:
        body = {
            "action": action,
            "payload": payload,
            "exp": int(time.time()) + self._ttl_seconds,
            "nonce": secrets.token_urlsafe(12),
        }
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self._secret, raw, hashlib.sha256).digest()
        return f"{_encode(raw)}.{_encode(signature)}"

    def consume(self, token: str, expected_action: str) -> dict[str, Any]:
        try:
            encoded_body, encoded_signature = token.split(".", 1)
            raw = _decode(encoded_body)
            signature = _decode(encoded_signature)
            body = json.loads(raw)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ConfirmationError("确认令牌格式无效") from exc
        expected = hmac.new(self._secret, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ConfirmationError("确认令牌签名无效")
        if body.get("action") != expected_action:
            raise ConfirmationError("确认令牌与当前操作不匹配")
        now = int(time.time())
        if int(body.get("exp", 0)) < now:
            raise ConfirmationError("确认令牌已过期，请重新预览操作")
        nonce = body.get("nonce")
        if not isinstance(nonce, str):
            raise ConfirmationError("确认令牌缺少 nonce")
        with self._lock:
            self._used_nonces = {
                key: exp for key, exp in self._used_nonces.items() if exp >= now
            }
            if nonce in self._used_nonces:
                raise ConfirmationError("确认令牌已经使用，禁止重复执行")
            self._used_nonces[nonce] = int(body["exp"])
        payload = body.get("payload")
        if not isinstance(payload, dict):
            raise ConfirmationError("确认令牌载荷无效")
        return payload


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
