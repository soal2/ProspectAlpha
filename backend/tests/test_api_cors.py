from fastapi.testclient import TestClient

from riskagent.api.main import create_app


def test_loopback_vite_origin_is_allowed_on_any_port(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://app.example.com")
    client = TestClient(create_app())

    response = client.options(
        "/analysis-runs",
        headers={
            "Origin": "http://localhost:5178",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5178"


def test_unconfigured_remote_origin_remains_rejected(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://app.example.com")
    client = TestClient(create_app())

    response = client.options(
        "/analysis-runs",
        headers={
            "Origin": "https://untrusted.example.net",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
