from pathlib import Path

from fastapi.testclient import TestClient

from riskagent.analysis_runs import AnalysisRunCreate, AnalysisRunRepository
from riskagent.api.main import create_app
from riskagent.risk_rules import RiskRuleRepository


def test_analysis_run_uses_rule_snapshot_and_evidence(tmp_path):
    rule_repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    repo = AnalysisRunRepository(tmp_path / "runs.yaml", rule_repo)

    run = repo.create_and_run(
        AnalysisRunCreate(
            company_name="测试 IPO",
            document_text="公司存在关联交易、赎回安排，经营活动现金流持续为负。",
            structured_data={"经营活动现金流": "连续两期为负"},
        )
    )

    assert run.status == "completed"
    assert run.rule_snapshot["totals"]["rule_count"] == 143
    assert run.findings
    assert all(finding.rule_id.startswith("HKIPO-") for finding in run.findings)
    assert any("关联交易" in finding.matched_terms for finding in run.findings)


def test_analysis_api_creates_completed_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ANALYSIS_RUNS_PATH", str(tmp_path / "runs.yaml"))
    monkeypatch.setenv("RISK_RULES_PATH", str(Path(__file__).parents[1] / "configs" / "risk_rules.yaml"))
    client = TestClient(create_app())

    response = client.post(
        "/analysis-runs",
        json={
            "company_name": "测试 IPO",
            "document_text": "招股书披露对赌和赎回安排，同时存在供应商集中度风险。",
            "structured_data": {"供应商集中度": "Top5 采购占比 80%"},
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["rule_snapshot"]["totals"]["rule_count"] == 143
    assert payload["findings"]

    run_id = payload["run_id"]
    findings = client.get(f"/analysis-runs/{run_id}/findings").json()["findings"]
    assert findings
