import asyncio

import pytest
from fastapi.testclient import TestClient

from riskagent.api.auth import read_limited_body, reset_auth_state
from riskagent.api.main import create_app

_ORIGIN = {"Origin": "http://localhost:5173"}
_PASSWORD = "x" * 16
_SECRET = "s" * 32


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("API_AUTH_DISABLED", "0")
    monkeypatch.setenv("API_AUTH_USERNAME", "operator")
    monkeypatch.setenv("API_AUTH_PASSWORD", _PASSWORD)
    monkeypatch.setenv("API_AUTH_SESSION_SECRET", _SECRET)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("API_AUTH_COOKIE_SAMESITE", "lax")
    monkeypatch.setenv("API_AUTH_COOKIE_SECURE", "0")
    reset_auth_state()


def test_production_refuses_to_start_without_auth_config(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("API_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("API_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("API_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("API_AUTH_SESSION_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="鉴权配置缺失"):
        create_app()


def test_production_rejects_local_opt_out(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_AUTH_DISABLED", "1")

    with pytest.raises(RuntimeError, match="不允许 API_AUTH_DISABLED"):
        create_app()


def test_development_without_opt_out_is_closed(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("API_AUTH_DISABLED", "0")
    monkeypatch.delenv("API_AUTH_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="鉴权配置缺失"):
        create_app()


def test_login_sets_httponly_cookie_and_hides_secrets(monkeypatch) -> None:
    _enable(monkeypatch)
    client = TestClient(create_app())
    response = client.post(
        "/auth/login",
        json={"username": "operator", "password": _PASSWORD},
        headers=_ORIGIN,
    )

    assert response.status_code == 200
    body = response.text
    assert _PASSWORD not in body
    assert _SECRET not in body
    assert "pa_session" not in body
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(item for item in cookies if item.lower().startswith("pa_session="))
    assert "httponly" in session_cookie.lower()
    assert "samesite=lax" in session_cookie.lower()
    assert _PASSWORD not in " ".join(cookies)
    assert response.json()["csrf_token"]


def test_unauthorized_requests_do_not_call_paid_clients(monkeypatch) -> None:
    _enable(monkeypatch)
    llm_calls: list[str] = []
    upload_calls: list[str] = []
    start_calls: list[str] = []

    def reject_llm():
        llm_calls.append("llm")
        raise AssertionError("未登录请求不应创建模型客户端")

    def reject_upload(**_kwargs):
        upload_calls.append("upload")
        raise AssertionError("未登录请求不应保存上传文件")

    def reject_start(*_args, **_kwargs):
        start_calls.append("start")
        raise AssertionError("未登录请求不应启动分析")

    monkeypatch.setattr("riskagent.api.chat.get_default_llm_client", reject_llm)
    monkeypatch.setattr("riskagent.api.documents.register_uploaded_prospectus", reject_upload)
    monkeypatch.setattr("riskagent.api.analysis.AnalysisRunRepository.start", reject_start)
    client = TestClient(create_app())

    chat = client.post("/chat", json={"message": "你好"}, headers=_ORIGIN)
    upload = client.post(
        "/documents/upload",
        files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
        headers=_ORIGIN,
    )
    started = client.post("/analysis-runs/run_missing/start", headers=_ORIGIN)

    assert chat.status_code == 401
    assert upload.status_code == 401
    assert started.status_code == 401
    assert llm_calls == []
    assert upload_calls == []
    assert start_calls == []


def test_csrf_and_origin_block_mutations_before_work(monkeypatch) -> None:
    _enable(monkeypatch)
    calls: list[str] = []

    def reject_llm():
        calls.append("llm")
        raise AssertionError("跨站请求不应创建模型客户端")

    monkeypatch.setattr("riskagent.api.chat.get_default_llm_client", reject_llm)
    client = TestClient(create_app())
    logged_in = client.post(
        "/auth/login",
        json={"username": "operator", "password": _PASSWORD},
        headers=_ORIGIN,
    )
    assert logged_in.status_code == 200

    missing_csrf = client.post("/chat", json={"message": "你好"}, headers=_ORIGIN)
    bad_origin = client.post(
        "/chat",
        json={"message": "你好"},
        headers={"Origin": "https://evil.example", "X-CSRF-Token": logged_in.json()["csrf_token"]},
    )

    assert missing_csrf.status_code == 403
    assert bad_origin.status_code == 403
    assert calls == []


def test_expired_session_is_rejected(monkeypatch) -> None:
    _enable(monkeypatch)
    from riskagent.api import auth as auth_module

    client = TestClient(create_app())
    logged_in = client.post(
        "/auth/login",
        json={"username": "operator", "password": _PASSWORD},
        headers=_ORIGIN,
    )
    assert logged_in.status_code == 200
    with auth_module._LOCK:
        for record in auth_module._SESSIONS.values():
            record.expires_at = 0
    again = client.get("/analysis-runs")
    assert again.status_code == 401
    assert _PASSWORD not in again.text
    assert _SECRET not in again.text


def test_logout_revokes_the_session(monkeypatch) -> None:
    _enable(monkeypatch)
    client = TestClient(create_app())
    logged_in = client.post(
        "/auth/login",
        json={"username": "operator", "password": _PASSWORD},
        headers=_ORIGIN,
    )
    csrf = logged_in.json()["csrf_token"]
    logout = client.post("/auth/logout", headers={**_ORIGIN, "X-CSRF-Token": csrf})
    assert logout.status_code == 200

    again = client.get("/analysis-runs")
    assert again.status_code == 401


def test_login_rate_limit(monkeypatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setenv("API_AUTH_LOGIN_FAILURE_LIMIT", "3")
    client = TestClient(create_app())
    for _ in range(3):
        failed = client.post(
            "/auth/login",
            json={"username": "operator", "password": "not-the-password"},
            headers=_ORIGIN,
        )
        assert failed.status_code == 401
        assert "not-the-password" not in failed.text
    blocked = client.post(
        "/auth/login",
        json={"username": "operator", "password": _PASSWORD},
        headers=_ORIGIN,
    )
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_health_and_ready_stay_open_when_auth_is_required(monkeypatch) -> None:
    _enable(monkeypatch)
    client = TestClient(create_app())
    health = client.get("/health")
    ready = client.get("/ready")
    assert health.status_code != 401
    assert ready.status_code != 401
    assert "password" not in ready.text.lower()
    assert "secret" not in ready.text.lower()


def test_body_limit_counts_bytes_without_content_length() -> None:
    async def chunks():
        messages = [
            {"type": "http.request", "body": b"abcdef", "more_body": True},
            {"type": "http.request", "body": b"ghij", "more_body": False},
        ]

        async def receive():
            return messages.pop(0)

        return await read_limited_body(receive, 8)

    assert asyncio.run(chunks()) is None
