"""Tests for the V2 reports API router.

These tests mount only the V2 router (not the full app) and inject test
doubles via monkeypatch so we don't need the full AnalysisRunRepository
database or runtime providers.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from riskagent.api import reports_v2 as reports_v2_api
from riskagent.api.reports_v2 import router as reports_v2_router
from riskagent.reporting.service import BuildRequest, make_service
from riskagent.reporting.store import DbReportStore, make_db_store
from riskagent.research.models import HypothesisStatus


_CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "report_templates"


def _clean(hyp):
    hyp.supporting_evidence_ids = []
    hyp.counter_evidence_ids = []
    return hyp


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_with_store(tmp_path):
    """Build a minimal FastAPI app with the V2 router and a file-backed store."""

    app = FastAPI()
    app.include_router(reports_v2_router)
    # Use a file-based SQLite so the schema persists across sessions.
    db_path = tmp_path / "reports_v2.db"
    store = DbReportStore(f"sqlite:///{db_path}")
    store.create_schema()
    service = make_service(_CONFIG_DIR)
    reports_v2_api._report_store = lambda: store
    reports_v2_api._reporting_service = lambda: service
    return app, store


@pytest.fixture
def client(app_with_store):
    app, _ = app_with_store
    return TestClient(app)


def _make_run(artifact, run_id="run_api", input_payload=None):
    """Build a SimpleNamespace that mimics the AnalysisRun fields the API reads."""

    research = SimpleNamespace(
        versions=[artifact],
        red_team_verdicts=[],
    )
    return SimpleNamespace(
        run_id=run_id,
        research=research,
        input_payload=input_payload,
    )


def _build_artifact(frozen_artifact_factory, hypothesis_factory):
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    return frozen_artifact_factory(hypotheses=[hyp])


def _install_run(client, app_with_store, run):
    """Patch the analysis repo to return ``run`` for ``run.run_id``."""

    app, _ = app_with_store
    fake_repo = SimpleNamespace(get=lambda run_id: run if run_id == run.run_id else (_ for _ in ()).throw(KeyError(run_id)))
    reports_v2_api._analysis_repo = lambda: fake_repo
    return run


# ---------------------------------------------------------------------------
# POST /v2/reports:build
# ---------------------------------------------------------------------------


def test_build_returns_201_for_complete_artifact(
    client, app_with_store, frozen_artifact_factory, hypothesis_factory
):
    artifact = _build_artifact(frozen_artifact_factory, hypothesis_factory)
    run = _install_run(client, app_with_store, _make_run(artifact))

    response = client.post(f"/analysis-runs/{run.run_id}/v2/reports:build")
    assert response.status_code == 201
    body = response.json()
    assert body["run_id"] == run.run_id
    assert body["report_version"] == 1
    assert body["research_artifact_version"] == artifact.version
    assert body["frozen"] is True


def test_build_returns_404_when_run_missing(
    client, app_with_store, frozen_artifact_factory, hypothesis_factory
):
    artifact = _build_artifact(frozen_artifact_factory, hypothesis_factory)
    run = _make_run(artifact, run_id="run_present")
    fake_repo = SimpleNamespace(
        get=lambda run_id: (_ for _ in ()).throw(KeyError(run_id))
    )
    reports_v2_api._analysis_repo = lambda: fake_repo

    response = client.post("/analysis-runs/run_missing/v2/reports:build")
    assert response.status_code == 404


def test_build_returns_409_when_no_research(client, app_with_store):
    run = SimpleNamespace(research=None, input_payload=None)
    fake_repo = SimpleNamespace(get=lambda run_id: run)
    reports_v2_api._analysis_repo = lambda: fake_repo

    response = client.post("/analysis-runs/run_x/v2/reports:build")
    assert response.status_code == 409


def test_build_increments_version_on_rebuild(
    client, app_with_store, frozen_artifact_factory, hypothesis_factory
):
    """Two builds against the same run produce v1 then v2."""

    artifact = _build_artifact(frozen_artifact_factory, hypothesis_factory)
    run = _install_run(client, app_with_store, _make_run(artifact))

    r1 = client.post(f"/analysis-runs/{run.run_id}/v2/reports:build")
    assert r1.status_code == 201
    assert r1.json()["report_version"] == 1

    r2 = client.post(f"/analysis-runs/{run.run_id}/v2/reports:build")
    assert r2.status_code == 201
    assert r2.json()["report_version"] == 2


# ---------------------------------------------------------------------------
# GET endpoints
# ---------------------------------------------------------------------------


def test_get_returns_404_for_missing_bundle(client):
    response = client.get("/analysis-runs/run_x/v2/reports/1")
    assert response.status_code == 404


def test_list_returns_empty_for_unknown_run(client):
    response = client.get("/analysis-runs/run_none/v2/reports")
    assert response.status_code == 200
    assert response.json() == []


def test_get_latest_returns_404_for_empty_run(client):
    response = client.get("/analysis-runs/run_none/v2/reports:latest")
    assert response.status_code == 404


def test_full_read_flow(
    client, app_with_store, frozen_artifact_factory, hypothesis_factory
):
    """Build, list, latest, get by version all return consistent data."""

    artifact = _build_artifact(frozen_artifact_factory, hypothesis_factory)
    run = _install_run(client, app_with_store, _make_run(artifact))

    # Build
    build = client.post(f"/analysis-runs/{run.run_id}/v2/reports:build")
    assert build.status_code == 201
    version = build.json()["report_version"]

    # List
    listed = client.get(f"/analysis-runs/{run.run_id}/v2/reports")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["report_version"] == version

    # Latest
    latest = client.get(f"/analysis-runs/{run.run_id}/v2/reports:latest")
    assert latest.status_code == 200
    assert latest.json()["report_version"] == version

    # Get by version
    fetched = client.get(
        f"/analysis-runs/{run.run_id}/v2/reports/{version}"
    )
    assert fetched.status_code == 200
    assert fetched.json()["report_version"] == version


# ---------------------------------------------------------------------------
# Export endpoint
# ---------------------------------------------------------------------------


def test_export_returns_markdown(
    client, app_with_store, frozen_artifact_factory, hypothesis_factory
):
    artifact = _build_artifact(frozen_artifact_factory, hypothesis_factory)
    run = _install_run(client, app_with_store, _make_run(artifact))

    client.post(f"/analysis-runs/{run.run_id}/v2/reports:build")
    response = client.get(
        f"/analysis-runs/{run.run_id}/v2/reports/1/export?format=markdown"
    )
    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]
    body = response.text
    assert "风险研究简报" in body
    assert "专项深度研究报告" in body


def test_export_rejects_unknown_format(
    client, app_with_store, frozen_artifact_factory, hypothesis_factory
):
    artifact = _build_artifact(frozen_artifact_factory, hypothesis_factory)
    _install_run(client, app_with_store, _make_run(artifact))
    response = client.get(
        "/analysis-runs/run_api/v2/reports/1/export?format=pdf"
    )
    assert response.status_code == 400


def test_export_returns_404_for_missing_bundle(client):
    response = client.get(
        "/analysis-runs/run_none/v2/reports/1/export?format=markdown"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Failed-artifact build path
# ---------------------------------------------------------------------------


def test_build_returns_409_for_failed_artifact(
    client, app_with_store, frozen_artifact_factory, hypothesis_factory
):
    """completion_status=failed surfaces as 409."""

    from riskagent.research.models import ResearchCompletionStatus

    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(
        hypotheses=[hyp],
        completion_status=ResearchCompletionStatus.failed,
    )
    run = _install_run(client, app_with_store, _make_run(artifact))
    response = client.post(f"/analysis-runs/{run.run_id}/v2/reports:build")
    assert response.status_code == 409
