from fastapi.testclient import TestClient

from riskagent.api.limits import ensure_within_count
from riskagent.api.main import create_app


def test_routes_stay_open_when_token_is_unset(monkeypatch) -> None:
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    client = TestClient(create_app())

    response = client.get("/analysis-runs")

    assert response.status_code != 401


def test_bearer_token_gates_routes_but_not_health_or_preflight(monkeypatch) -> None:
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://app.example.com")
    client = TestClient(create_app())

    denied = client.get("/analysis-runs")
    assert denied.status_code == 401

    wrong = client.get("/analysis-runs", headers={"Authorization": "Bearer other-token"})
    assert wrong.status_code == 401

    allowed = client.get("/analysis-runs", headers={"Authorization": "Bearer test-token"})
    assert allowed.status_code != 401

    health = client.get("/health")
    assert health.status_code != 401

    preflight = client.options(
        "/analysis-runs",
        headers={
            "Origin": "http://localhost:5178",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert preflight.status_code == 200


def test_json_and_upload_limits_reject_oversized_bodies(monkeypatch) -> None:
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("API_MAX_JSON_BYTES", "32")
    monkeypatch.setenv("API_MAX_UPLOAD_BYTES", "32")
    client = TestClient(create_app())

    oversized_json = client.post("/analysis-runs", json={"company_name": "x" * 80, "document_text": "y" * 80})
    assert oversized_json.status_code == 413

    oversized_upload = client.post(
        "/documents/upload",
        files={"file": ("prospectus.pdf", b"%PDF-" + b"x" * 80, "application/pdf")},
    )
    assert oversized_upload.status_code == 413


def test_stored_object_cap_can_be_configured() -> None:
    try:
        ensure_within_count(2, 2, "文档")
    except Exception as exc:  # noqa: BLE001 - assert the HTTP error contract
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("object cap must reject the next object")
