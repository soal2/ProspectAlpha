from pathlib import Path
import time

from fastapi.testclient import TestClient

from riskagent.analysis_runs import AnalysisRunCreate
from riskagent.api.main import create_app
from riskagent.risk_rules import RiskRuleRepository


def test_analysis_run_uses_rule_snapshot_and_evidence(tmp_path, analysis_repo_factory):
    rule_repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    repo = analysis_repo_factory(
        rule_repository=rule_repo,
        path=tmp_path / "runs.yaml",
    )

    run = repo.create_and_run(
        AnalysisRunCreate(
            company_name="测试 IPO",
            document_id="doc_test",
            document_text="公司存在关联交易、赎回安排，经营活动现金流持续为负。",
            structured_data={"经营活动现金流": "连续两期为负"},
        )
    )

    assert run.status == "completed"
    assert run.rule_snapshot["totals"]["rule_count"] == 143
    assert run.findings
    assert all(finding.rule_id.startswith("HKIPO-") for finding in run.findings)
    assert any("关联交易" in finding.matched_terms for finding in run.findings)


def test_analysis_api_creates_ready_run_then_start_completes(
    tmp_path, monkeypatch, analysis_repo_factory
):
    monkeypatch.setenv("ANALYSIS_RUNS_PATH", str(tmp_path / "runs.yaml"))
    monkeypatch.setenv("RISK_RULES_PATH", str(Path(__file__).parents[1] / "configs" / "risk_rules.yaml"))
    monkeypatch.setenv("USE_DATABASE", "0")
    repo = analysis_repo_factory(
        rule_repository=RiskRuleRepository(
            Path(__file__).parents[1] / "configs" / "risk_rules.yaml"
        ),
        path=tmp_path / "runs.yaml",
        evidence_library_path=tmp_path / "evidence.yaml",
    )
    monkeypatch.setattr("riskagent.api.analysis._repo", lambda: repo)
    client = TestClient(create_app())

    response = client.post(
        "/analysis-runs",
        json={
            "company_name": "测试 IPO",
            "document_id": "doc_test",
            "document_text": "招股书披露对赌和赎回安排，同时存在供应商集中度风险。",
            "structured_data": {"供应商集中度": "Top5 采购占比 80%"},
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["rule_snapshot"]["totals"]["rule_count"] == 143
    assert payload["findings"] == []

    run_id = payload["run_id"]
    started = client.post(f"/analysis-runs/{run_id}/start").json()
    assert started["status"] in {"queued", "completed"}
    for _ in range(50):
        current = client.get(f"/analysis-runs/{run_id}").json()
        if current["status"] == "completed":
            break
        time.sleep(0.1)
    else:
        raise AssertionError("analysis run did not complete")
    findings = client.get(f"/analysis-runs/{run_id}/findings").json()["findings"]
    assert findings


def test_analysis_options_returns_minimal_real_choices(monkeypatch):
    monkeypatch.setenv("USE_DATABASE", "0")
    monkeypatch.setenv("RISK_RULES_PATH", str(Path(__file__).parents[1] / "configs" / "risk_rules.yaml"))
    client = TestClient(create_app())

    response = client.get("/analysis-options")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"risk_rule_sets", "agents", "analysis_depths"}
    assert payload["risk_rule_sets"][0]["rule_set_id"] == "hk_ipo_prospectus_risk_framework_v1"
    assert "rules" not in payload["risk_rule_sets"][0]
    assert {agent["agent_name"] for agent in payload["agents"]}.issuperset(
        {"financial_agent", "legal_agent", "business_agent", "market_agent"}
    )
    assert {item["depth"] for item in payload["analysis_depths"]} == {"quick", "standard", "deep"}


def test_analysis_create_rejects_legacy_preselected_expert_chain(monkeypatch):
    monkeypatch.setenv("USE_DATABASE", "0")
    client = TestClient(create_app())

    response = client.post(
        "/analysis-runs",
        json={
            "company_name": "测试 IPO",
            "document_text": "招股书正文",
            "selected_agent_names": ["financial_agent", "legal_agent"],
        },
    )

    assert response.status_code == 422


def test_analysis_run_persists_dynamic_strategy_agents_and_depth(
    tmp_path, analysis_repo_factory
):
    rule_repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    repo = analysis_repo_factory(
        rule_repository=rule_repo,
        selected_agents=("legal_agent",),
        path=tmp_path / "runs.yaml",
    )

    run = repo.create_and_run(
        AnalysisRunCreate(
            company_name="测试 IPO",
            document_id="doc_test",
            document_text="公司存在关联交易、赎回安排，经营活动现金流持续为负。",
            risk_rule_set_id="hk_ipo_prospectus_risk_framework_v1",
            analysis_depth="quick",
            max_findings=40,
        )
    )

    assert run.status == "completed"
    assert run.analysis_config["analysis_depth"] == "quick"
    assert run.analysis_config["max_findings"] == 8
    assert run.analysis_config["selected_agents"] == [
        {"agent_name": "legal_agent", "title": "Legal and compliance specialist"}
    ]
    assert {finding.agent_name for finding in run.findings} <= {"legal_agent"}


def test_dynamic_domain_skills_keep_rule_counts_and_skill_ids(
    tmp_path, analysis_repo_factory
):
    rule_repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    repo = analysis_repo_factory(
        rule_repository=rule_repo,
        selected_agents=("financial_agent", "legal_agent"),
        path=tmp_path / "runs.yaml",
    )

    run = repo.create_and_run(
        AnalysisRunCreate(
            company_name="Skill 测试 IPO",
            document_id="doc_test",
            document_text="风险因素\n公司存在关联交易、赎回安排，经营活动现金流持续为负。",
            analysis_depth="standard",
        )
    )

    assert run.status == "completed"
    assert all(finding.skill_id for finding in run.findings)
    assert {finding.skill_id for finding in run.findings}.issubset(
        {"financial_risk_skill", "legal_compliance_skill"}
    )
    assert {item["skill_id"] for item in run.module_results}.issuperset(
        {"financial_risk_skill", "legal_compliance_skill"}
    )
    assert all(item["rules_read"] > 0 for item in run.module_results)
    assert any(event["event_type"] == "peer_request_sent" for event in run.events)


def test_invalid_strategy_pauses_and_api_selection_resumes(
    tmp_path, monkeypatch, analysis_repo_factory
):
    def invalid_strategy(**_kwargs):
        raise ValueError("invalid structured strategy")

    repo = analysis_repo_factory(
        rule_repository=RiskRuleRepository(
            Path(__file__).parents[1] / "configs" / "risk_rules.yaml"
        ),
        strategy_provider=invalid_strategy,
        path=tmp_path / "runs.yaml",
        evidence_library_path=tmp_path / "evidence.yaml",
    )
    monkeypatch.setenv("USE_DATABASE", "0")
    monkeypatch.setenv("ANALYSIS_API_EXECUTION_MODE", "inline")
    monkeypatch.setattr("riskagent.api.analysis._repo", lambda: repo)
    client = TestClient(create_app())

    created = client.post(
        "/analysis-runs",
        json={
            "company_name": "人工恢复 IPO",
            "document_id": "doc_test",
            "document_text": "公司存在关联交易和赎回安排。",
        },
    ).json()
    paused = client.post(f"/analysis-runs/{created['run_id']}/start")

    assert paused.status_code == 200
    assert paused.json()["status"] == "awaiting_expert_selection"
    assert "invalid structured strategy" in paused.json()["interrupt_reason"]

    duplicate = client.post(
        f"/analysis-runs/{created['run_id']}/expert-selection",
        json={"agent_names": ["legal_agent", "legal_agent"]},
    )
    assert duplicate.status_code == 409

    resumed = client.post(
        f"/analysis-runs/{created['run_id']}/expert-selection",
        json={"agent_names": ["legal_agent"]},
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "completed"
    assert resumed.json()["strategy_plan"]["selected_agents"] == ["legal_agent"]
    signatures = [
        (
            item.get("created_at"),
            item["event_type"],
            item["actor_name"],
            item["title"],
        )
        for item in resumed.json()["events"]
    ]
    assert len(signatures) == len(set(signatures))
