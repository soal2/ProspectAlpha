"""Single-user session gate.

The browser never receives a build-time secret. It signs in at runtime; the
server stores the session and sets an expiring HttpOnly cookie. The password
and session secret stay in backend environment variables.

This is one shared operator for the whole deployment, not per-user ownership.
Every document and analysis run is visible to that single signed-in operator.
Resource limits are deployment-wide for the same reason.

Local open access exists only when ``APP_ENV`` is not ``production`` and
``API_AUTH_DISABLED=1``. Production ignores that opt-out and refuses to start
when the auth settings are missing.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from riskagent.api.limits import max_json_bytes, max_upload_bytes

SESSION_COOKIE = "pa_session"
CSRF_COOKIE = "pa_csrf"
CSRF_HEADER = "x-csrf-token"
_LOCAL_ORIGIN = re.compile(r"^https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?$")
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class SessionRecord:
    session_id: str
    csrf_token: str
    expires_at: float

    def active(self, now: float | None = None) -> bool:
        return self.expires_at > (now if now is not None else time.time())


_LOCK = threading.Lock()
_SESSIONS: dict[str, SessionRecord] = {}
_FAILURES: dict[str, list[float]] = {}


def reset_auth_state() -> None:
    """Drop in-memory sessions and login failures. Tests use this."""

    with _LOCK:
        _SESSIONS.clear()
        _FAILURES.clear()


def auth_disabled() -> bool:
    if _production():
        return False
    return os.getenv("API_AUTH_DISABLED", "").strip() == "1"


def _production() -> bool:
    return os.getenv("APP_ENV", "").strip().lower() == "production"


def _cookie_samesite() -> str:
    value = os.getenv("API_AUTH_COOKIE_SAMESITE", "lax").strip().lower() or "lax"
    if value not in {"lax", "strict", "none"}:
        return "lax"
    return value


def cookie_secure() -> bool:
    forced = os.getenv("API_AUTH_COOKIE_SECURE", "auto").strip().lower()
    if _production() or forced == "1":
        return True
    if forced == "0":
        return False
    return False


def _ttl_seconds() -> int:
    raw = os.getenv("API_AUTH_SESSION_TTL_SECONDS", "28800").strip()
    try:
        ttl = int(raw)
    except ValueError:
        ttl = 28800
    return max(60, min(ttl, 7 * 24 * 3600))


def _failure_limit() -> int:
    raw = os.getenv("API_AUTH_LOGIN_FAILURE_LIMIT", "5").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 5


def _failure_window() -> int:
    raw = os.getenv("API_AUTH_LOGIN_FAILURE_WINDOW_SECONDS", "900").strip()
    try:
        return max(30, int(raw))
    except ValueError:
        return 900


def validate_auth_config() -> None:
    """Fail closed unless auth is fully configured or local opt-out is explicit."""

    if _production() and os.getenv("API_AUTH_DISABLED", "").strip() == "1":
        raise RuntimeError("生产环境不允许 API_AUTH_DISABLED。")
    if auth_disabled():
        return
    missing = [
        name
        for name in ("API_AUTH_USERNAME", "API_AUTH_PASSWORD", "API_AUTH_SESSION_SECRET")
        if not os.getenv(name, "").strip()
    ]
    if missing:
        raise RuntimeError("鉴权配置缺失，已拒绝启动：" + ", ".join(missing))
    username = os.getenv("API_AUTH_USERNAME", "")
    password = os.getenv("API_AUTH_PASSWORD", "")
    secret = os.getenv("API_AUTH_SESSION_SECRET", "")
    if any(char in value for value in (username, password, secret) for char in ("\r", "\n")):
        raise RuntimeError("鉴权配置包含换行，已拒绝启动。")
    if len(password) < 12:
        raise RuntimeError("API_AUTH_PASSWORD 长度不足，已拒绝启动。")
    if len(secret) < 32:
        raise RuntimeError("API_AUTH_SESSION_SECRET 长度不足，已拒绝启动。")
    samesite = os.getenv("API_AUTH_COOKIE_SAMESITE", "lax").strip().lower() or "lax"
    if samesite not in {"lax", "strict", "none"}:
        raise RuntimeError("API_AUTH_COOKIE_SAMESITE 只能是 lax、strict 或 none。")
    if samesite == "none" and not cookie_secure():
        raise RuntimeError("SameSite=None 必须同时使用 Secure。")


def _digest(value: str) -> bytes:
    secret = os.getenv("API_AUTH_SESSION_SECRET", "").encode()
    return hashlib.scrypt(value.encode(), salt=secret[:16].ljust(16, b"s"), n=2**14, r=8, p=1, dklen=32)


def _credentials_match(username: str, password: str) -> bool:
    expected_user = os.getenv("API_AUTH_USERNAME", "")
    expected_password = os.getenv("API_AUTH_PASSWORD", "")
    user_ok = hmac.compare_digest(_digest(username), _digest(expected_user))
    password_ok = hmac.compare_digest(_digest(password), _digest(expected_password))
    return bool(user_ok and password_ok)


def _sign(session_id: str) -> str:
    secret = os.getenv("API_AUTH_SESSION_SECRET", "").encode()
    mac = hmac.new(secret, session_id.encode(), hashlib.sha256).hexdigest()
    return f"{session_id}.{mac}"


def _unsign(token: str) -> str | None:
    session_id, separator, mac = token.partition(".")
    if not separator or not session_id or not mac:
        return None
    secret = os.getenv("API_AUTH_SESSION_SECRET", "").encode()
    expected = hmac.new(secret, session_id.encode(), hashlib.sha256).hexdigest()
    if len(mac) != len(expected) or not hmac.compare_digest(mac, expected):
        return None
    return session_id


def issue_session() -> SessionRecord:
    now = time.time()
    record = SessionRecord(
        session_id=secrets.token_urlsafe(32),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=now + _ttl_seconds(),
    )
    with _LOCK:
        _purge_locked(now)
        _SESSIONS[record.session_id] = record
    return record


def revoke_session(session_id: str) -> None:
    with _LOCK:
        _SESSIONS.pop(session_id, None)


def get_session(session_id: str) -> SessionRecord | None:
    now = time.time()
    with _LOCK:
        record = _SESSIONS.get(session_id)
        if record is None:
            return None
        if not record.active(now):
            _SESSIONS.pop(session_id, None)
            return None
        return record


def _purge_locked(now: float) -> None:
    expired = [key for key, record in _SESSIONS.items() if not record.active(now)]
    for key in expired:
        _SESSIONS.pop(key, None)
    for key, stamps in list(_FAILURES.items()):
        fresh = [stamp for stamp in stamps if now - stamp < _failure_window()]
        if fresh:
            _FAILURES[key] = fresh
        else:
            _FAILURES.pop(key, None)


def client_ip(scope: dict[str, Any]) -> str:
    headers = _headers(scope)
    if os.getenv("API_AUTH_TRUST_PROXY", "").strip() == "1":
        forwarded = headers.get("x-forwarded-for", "")
        parts = [item.strip() for item in forwarded.split(",") if item.strip()]
        if parts:
            return parts[-1][:64]
    client = scope.get("client")
    if isinstance(client, tuple) and client:
        return str(client[0])[:64]
    return "unknown"


def login_blocked(ip: str) -> int:
    """Return retry-after seconds when the IP is locked, else 0."""

    now = time.time()
    window = _failure_window()
    with _LOCK:
        stamps = [stamp for stamp in _FAILURES.get(ip, []) if now - stamp < window]
        _FAILURES[ip] = stamps
        if len(stamps) < _failure_limit():
            return 0
        return max(1, int(window - (now - stamps[0])))


def record_login_failure(ip: str) -> None:
    now = time.time()
    with _LOCK:
        stamps = [stamp for stamp in _FAILURES.get(ip, []) if now - stamp < _failure_window()]
        stamps.append(now)
        _FAILURES[ip] = stamps


def clear_login_failures(ip: str) -> None:
    with _LOCK:
        _FAILURES.pop(ip, None)


def _headers(scope: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in scope.get("headers") or []:
        found[key.decode("latin1").lower()] = value.decode("latin1")
    return found


def _cookies(header: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for part in header.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name:
            found[name] = value
    return found


def session_from_scope(scope: dict[str, Any]) -> SessionRecord | None:
    if auth_disabled():
        return None
    token = _cookies(_headers(scope).get("cookie", "")).get(SESSION_COOKIE, "")
    session_id = _unsign(token)
    if session_id is None:
        return None
    return get_session(session_id)


def _origin_of(url: str) -> str | None:
    match = re.match(r"^(https?://[^/]+)", url.strip())
    if match is None:
        return None
    return match.group(1)


def origin_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    configured = [
        item.strip().rstrip("/")
        for item in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
        if item.strip()
    ]
    normalized = origin.rstrip("/")
    if normalized in configured:
        return True
    if not _production() and _LOCAL_ORIGIN.match(normalized):
        return True
    return False


def _request_origin(headers: dict[str, str]) -> str | None:
    origin = headers.get("origin", "").strip()
    if origin:
        return origin
    return _origin_of(headers.get("referer", ""))


def csrf_ok(scope: dict[str, Any], record: SessionRecord) -> bool:
    presented = _headers(scope).get(CSRF_HEADER, "")
    expected = record.csrf_token
    if not presented or len(presented) != len(expected):
        return False
    return hmac.compare_digest(presented, expected)


def apply_session_cookies(response: JSONResponse, record: SessionRecord) -> None:
    max_age = max(1, int(record.expires_at - time.time()))
    secure = cookie_secure()
    samesite = _cookie_samesite()
    response.set_cookie(
        key=SESSION_COOKIE,
        value=_sign(record.session_id),
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
    )
    response.set_cookie(
        key=CSRF_COOKIE,
        value=record.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite=samesite,
        path="/",
    )


def clear_session_cookies(response: JSONResponse) -> None:
    secure = cookie_secure()
    samesite = _cookie_samesite()
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure, httponly=True, samesite=samesite)
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, httponly=False, samesite=samesite)


async def read_limited_body(receive: Any, limit: int) -> bytes | None:
    """Read a request body, returning None when it exceeds ``limit``."""

    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return b"".join(chunks)
        if message["type"] != "http.request":
            continue
        chunk = message.get("body", b"")
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def body_limit_for(content_type: str) -> int:
    if "multipart/form-data" in content_type:
        return max_upload_bytes() + 65_536
    if "application/json" in content_type or not content_type:
        return max_json_bytes()
    return max_upload_bytes()


def _public_path(path: str, method: str) -> bool:
    if path == "/health":
        return True
    if path == "/auth/login" and method == "POST":
        return True
    if path == "/auth/session" and method == "GET":
        return True
    return False


class SingleUserAuthMiddleware:
    """ASGI middleware. It does not buffer streaming responses."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        method = (scope.get("method") or "GET").upper()
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        content_type = headers.get("content-type", "")
        if method in _UNSAFE_METHODS:
            limit = body_limit_for(content_type)
            length_text = headers.get("content-length", "")
            if length_text.isdigit() and int(length_text) > limit:
                await _send_json(send, 413, {"detail": "请求体超过大小限制。"})
                return
            body = await read_limited_body(receive, limit)
            if body is None:
                await _send_json(send, 413, {"detail": "请求体超过大小限制。"})
                return
            receive = _replay(body)
        if auth_disabled() or _public_path(path, method):
            await self.app(scope, receive, send)
            return
        record = session_from_scope(scope)
        if record is None:
            await _send_json(send, 401, {"detail": "未登录"})
            return
        if method in _UNSAFE_METHODS:
            origin = _request_origin(headers)
            if not origin_allowed(origin) or not csrf_ok(scope, record):
                await _send_json(send, 403, {"detail": "跨站请求被拒绝"})
                return
        await self.app(scope, receive, send)


def _replay(body: bytes) -> Any:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            # The client is still connected while the response streams.
            # A synthetic disconnect here cancels SSE before any chunk is sent.
            await asyncio.Event().wait()
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


async def _send_json(send: Any, status_code: int, payload: dict[str, str], extra: list[tuple[bytes, bytes]] | None = None) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header_list = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
        *(extra or []),
    ]
    await send({"type": "http.response.start", "status": status_code, "headers": header_list})
    await send({"type": "http.response.body", "body": body})


router = APIRouter(tags=["auth"])


@router.post("/auth/login")
async def login(request: Request) -> JSONResponse:
    if not origin_allowed(_request_origin(_headers(request.scope))):
        return JSONResponse(status_code=403, content={"detail": "登录来源不被允许"})
    ip = client_ip(request.scope)
    retry_after = login_blocked(ip)
    if retry_after:
        response = JSONResponse(status_code=429, content={"detail": "登录尝试过多，请稍后再试。"})
        response.headers["Retry-After"] = str(retry_after)
        return response
    try:
        payload = await request.json()
    except Exception:
        record_login_failure(ip)
        return JSONResponse(status_code=401, content={"detail": "用户名或密码不正确"})
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    if len(username) > 128 or len(password) > 256 or not _credentials_match(username, password):
        record_login_failure(ip)
        return JSONResponse(status_code=401, content={"detail": "用户名或密码不正确"})
    clear_login_failures(ip)
    record = issue_session()
    response = JSONResponse(
        content={
            "authenticated": True,
            "auth_disabled": False,
            "csrf_token": record.csrf_token,
            "expires_at": int(record.expires_at),
        }
    )
    apply_session_cookies(response, record)
    return response


@router.get("/auth/session")
def current_session(request: Request) -> JSONResponse:
    if auth_disabled():
        return JSONResponse({"authenticated": True, "auth_disabled": True, "csrf_token": None})
    record = session_from_scope(request.scope)
    if record is None:
        return JSONResponse({"authenticated": False, "auth_disabled": False})
    return JSONResponse(
        {
            "authenticated": True,
            "auth_disabled": False,
            "csrf_token": record.csrf_token,
            "expires_at": int(record.expires_at),
        }
    )


@router.post("/auth/logout")
def logout(request: Request) -> JSONResponse:
    record = session_from_scope(request.scope)
    if record is not None:
        revoke_session(record.session_id)
    response = JSONResponse({"authenticated": False})
    clear_session_cookies(response)
    return response
