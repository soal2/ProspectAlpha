from pathlib import Path
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from riskagent.analysis_runs import AnalysisRunCreate, AnalysisRunInputPatch
from riskagent.api.analysis import _api_dispatch_mode
from riskagent.api.main import create_app
from riskagent.common.cache import TTLCache
from riskagent.risk_rules import RiskRuleRepository


def _rule_repo() -> RiskRuleRepository:
    return RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")


def test_api_dispatch_mode_falls_back_to_shared_execution_setting(monkeypatch):
    monkeypatch.delenv("ANALYSIS_API_EXECUTION_MODE", raising=False)
    monkeypatch.setenv("ANALYSIS_EXECUTION_MODE", "rq")

    assert _api_dispatch_mode() == "rq"


def test_clarifying_patch_start_freezes_inputs_and_generates_artifacts(
    tmp_path, analysis_repo_factory
):
    repo = analysis_repo_factory(
        rule_repository=_rule_repo(),
        path=tmp_path / "runs.yaml",
        evidence_library_path=tmp_path / "evidence_library.yaml",
    )

    run = repo.create_draft(AnalysisRunCreate(company_name="待确认公司", document_text="", confirm_defaults=False))
    assert run.status == "clarifying"
    assert len(run.blocking_questions) == 3

    ready = repo.update_inputs(
        run.run_id,
        AnalysisRunInputPatch(
            company_name="测试 IPO",
            document_text="风险因素\n公司存在关联交易、对赌和赎回安排，经营活动现金流持续为负。",
            confirm_defaults=True,
            structured_data={"经营活动现金流": "连续两期为负"},
        ),
    )
    assert ready.status == "ready"

    completed = repo.start(run.run_id)
    assert completed.status == "completed"
    assert completed.inputs_frozen is True
    assert completed.document_blocks
    assert completed.findings
    assert completed.evidence
    assert completed.report is not None
    assert completed.report.review_status == "draft"
    assert all(item.text for item in completed.evidence)
    evidence_library = yaml.safe_load((tmp_path / "evidence_library.yaml").read_text()) or []
    assert evidence_library

    with pytest.raises(ValueError):
        repo.update_inputs(completed.run_id, AnalysisRunInputPatch(company_name="不可修改"))


def test_analysis_api_exposes_minimal_pipeline_resources(
    tmp_path, monkeypatch, analysis_repo_factory
):
    monkeypatch.setenv("ANALYSIS_RUNS_PATH", str(tmp_path / "runs.yaml"))
    monkeypatch.setenv("EVIDENCE_LIBRARY_PATH", str(tmp_path / "evidence_library.yaml"))
    monkeypatch.setenv("RISK_RULES_PATH", str(Path(__file__).parents[1] / "configs" / "risk_rules.yaml"))
    monkeypatch.setenv("USE_DATABASE", "0")
    repo = analysis_repo_factory(
        rule_repository=_rule_repo(),
        path=tmp_path / "runs.yaml",
        evidence_library_path=tmp_path / "evidence_library.yaml",
    )
    monkeypatch.setattr("riskagent.api.analysis._repo", lambda: repo)
    client = TestClient(create_app())

    response = client.post(
        "/analysis-runs",
        json={
            "company_name": "测试 IPO",
            "document_text": "风险因素\n招股书披露对赌、赎回、关联交易和供应商集中度风险。",
            "structured_data": {"供应商集中度": "Top5 采购占比 80%"},
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert "input_payload" not in payload
    assert payload["rule_snapshot"]["totals"]["rule_count"] == 143
    assert payload["status"] == "ready"
    assert payload["findings"] == []

    run_id = payload["run_id"]
    started = client.post(f"/analysis-runs/{run_id}/start").json()
    assert started["status"] in {"queued", "completed"}
    for _ in range(50):
        current = client.get(f"/analysis-runs/{run_id}").json()
        if current["status"] == "completed":
            started = current
            break
        time.sleep(0.1)
    else:
        raise AssertionError("analysis run did not complete")
    events = client.get(f"/analysis-runs/{run_id}/events").json()["events"]
    assert events
    assert {event["actor_name"] for event in events}.issuperset(
        {"financial_agent", "legal_agent", "business_agent", "market_agent", "orchestrator"}
    )
    assert client.get(f"/analysis-runs/{run_id}/evidence").json()
    trace = client.get(f"/analysis-runs/{run_id}/trace").json()
    assert set(trace) == {"stages", "seed", "flow", "evidenceFeed"}
    assert trace["stages"][-1] == {
        "key": "completed",
        "label": "交付完成",
        "owner": "analysis_run",
        "status": "done",
        "cost": None,
    }
    assert {event["event_type"] for event in events}.issuperset(
        {"strategy_decision_validated", "peer_request_sent", "dynamic_team_completed"}
    )
    assert trace["evidenceFeed"]
    assert "text" not in trace["evidenceFeed"][0]
    report = client.get(f"/analysis-runs/{run_id}/report").json()
    assert report["review_status"] == "draft"
    assert "IPO 风险分析草案" in report["markdown"]


def test_ttl_cache_prunes_under_pressure():
    cache: TTLCache[str] = TTLCache(ttl_seconds=0.001, max_items=16)
    for idx in range(100):
        cache.set(f"k{idx}", "x" * 100)
    assert len(cache) <= 16
    cache.collect_garbage()
    assert len(cache) <= 16
    cache.clear()
    assert len(cache) == 0
