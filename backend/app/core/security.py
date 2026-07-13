from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from threading import RLock
from typing import Any

from app.core.config import get_settings


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def sign_value(payload: dict[str, Any]) -> str:
    secret = get_settings().session_secret.encode("utf-8")
    payload_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    body = _b64url(payload_bytes)
    signature = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url(signature)}"


def unsign_value(token: str) -> dict[str, Any] | None:
    try:
        body, signature = token.split(".", 1)
    except ValueError:
        return None
    secret = get_settings().session_secret.encode("utf-8")
    expected = _b64url(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        payload = json.loads(_b64decode(body))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    expires_at = payload.get("exp")
    if isinstance(expires_at, (int, float)) and expires_at < time.time():
        return None
    return payload


class SessionStore:
    def __init__(self, path: Path | None = None):
        self.path = path or get_settings().session_store_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def create(self, user_id: int) -> str:
        with self._lock:
            settings = get_settings()
            session_id = secrets.token_urlsafe(32)
            now = int(time.time())
            sessions = self._read()
            sessions[session_id] = {
                "user_id": user_id,
                "created_at": now,
                "expires_at": now + settings.session_ttl_seconds,
            }
            self._write(self._without_expired(sessions))
            return sign_value({"sid": session_id, "exp": now + settings.session_ttl_seconds})

    def get_user_id(self, token: str | None) -> int | None:
        if not token:
            return None
        payload = unsign_value(token)
        if not payload:
            return None
        session_id = payload.get("sid")
        if not isinstance(session_id, str):
            return None
        with self._lock:
            sessions = self._without_expired(self._read())
            session = sessions.get(session_id)
            self._write(sessions)
            if not session:
                return None
            user_id = session.get("user_id")
            return int(user_id) if isinstance(user_id, int) else None

    def delete(self, token: str | None) -> None:
        if not token:
            return
        payload = unsign_value(token)
        if not payload or not isinstance(payload.get("sid"), str):
            return
        with self._lock:
            sessions = self._read()
            sessions.pop(payload["sid"], None)
            self._write(self._without_expired(sessions))

    @staticmethod
    def _without_expired(sessions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        now = time.time()
        return {
            key: value
            for key, value in sessions.items()
            if isinstance(value, dict) and float(value.get("expires_at", 0)) > now
        }


session_store = SessionStore()
