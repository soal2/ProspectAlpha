"""Optional single-user bearer gate.

The token is read from ``API_AUTH_TOKEN``. An empty value leaves the API open,
which matches deployments that have not set a secret. ``/health`` stays
unauthenticated so a platform health check does not need the token.
"""

from __future__ import annotations

import hmac
import json
import os

from riskagent.api.limits import max_json_bytes, max_upload_bytes


def _headers(scope: dict) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in scope.get("headers") or []:
        found[key.decode("latin1").lower()] = value.decode("latin1")
    return found


def _token_matches(presented: str, expected: str) -> bool:
    if len(presented) != len(expected):
        return False
    return hmac.compare_digest(presented, expected)


def _json_response(status_code: int, payload: dict[str, str]) -> list[tuple[bytes, bytes]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ], body


class SingleUserAuthMiddleware:
    """ASGI middleware that does not buffer streaming responses."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        method = scope.get("method") or ""
        if method == "OPTIONS" or path == "/health":
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        expected = os.getenv("API_AUTH_TOKEN", "").strip()
        if expected:
            authorization = headers.get("authorization", "")
            scheme, _, presented = authorization.partition(" ")
            if scheme.lower() != "bearer" or not _token_matches(presented.strip(), expected):
                await _send_json(send, 401, {"detail": "未授权"})
                return
        content_type = headers.get("content-type", "")
        length_text = headers.get("content-length", "")
        length = int(length_text) if length_text.isdigit() else None
        if method in {"POST", "PUT", "PATCH"} and length is not None:
            if "application/json" in content_type and length > max_json_bytes():
                await _send_json(send, 413, {"detail": "请求体超过 JSON 大小限制。"})
                return
            if "multipart/form-data" in content_type and length > max_upload_bytes() + 65_536:
                await _send_json(send, 413, {"detail": "上传超过大小限制。"})
                return
        await self.app(scope, receive, send)


async def _send_json(send, status_code: int, payload: dict[str, str]) -> None:
    header_list, body = _json_response(status_code, payload)
    await send({"type": "http.response.start", "status": status_code, "headers": header_list})
    await send({"type": "http.response.body", "body": body})
