from fastapi.testclient import TestClient

from riskagent.api.main import create_app


def test_vercel_startup_skips_schema_initialization(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr("riskagent.database.init_db", lambda: calls.append("database"))
    monkeypatch.setattr(
        "riskagent.documents.recover_interrupted_prospectus_jobs",
        lambda: calls.append("recovery"),
    )
    monkeypatch.setattr(
        "riskagent.workers.documents.start_document_dispatcher",
        lambda: calls.append("dispatcher"),
    )
    monkeypatch.setattr(
        "riskagent.parsing.mineru_runtime_health",
        lambda: (_ for _ in ()).throw(AssertionError("MinerU probe should not run")),
    )

    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200

    assert calls == []


def test_cors_preflight_is_cached_for_a_day(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://soal2.github.io")
    with TestClient(create_app()) as client:
        response = client.options(
            "/agents",
            headers={
                "Origin": "https://soal2.github.io",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-max-age"] == "86400"
