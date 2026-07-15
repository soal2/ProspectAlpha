from pathlib import Path
import time

from fastapi.testclient import TestClient
import yaml

from riskagent.agents import debate as debate_module
from riskagent.agents.exceptions import DebateLLMError
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
    for _ in range(200):
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
    assert len(payload["agents"]) == 12
    assert {agent["agent_name"] for agent in payload["agents"]} == {
        "shen_beichen_orchestrator_agent",
        "xiao_zeming_quality_agent",
        "zhou_shenyan_redteam_agent",
        "su_zhiji_business_agent",
        "jiang_hengzhi_market_agent",
        "gu_lingsheng_market_agent",
        "qin_bingwen_financial_agent",
        "lu_guanlan_market_agent",
        "wen_shouzheng_legal_agent",
        "ye_zhiyuan_business_agent",
        "cheng_zheng_evidence_agent",
        "bai_shice_market_agent",
    }
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


def test_analysis_list_tolerates_legacy_persisted_preselected_expert_chain(
    tmp_path, monkeypatch, analysis_repo_factory
):
    monkeypatch.setenv("USE_DATABASE", "0")
    rule_repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    path = tmp_path / "runs.yaml"
    repo = analysis_repo_factory(rule_repository=rule_repo, path=path)
    run = repo.create_draft(
        AnalysisRunCreate(company_name="历史 IPO", document_text="历史招股书正文")
    )
    persisted = run.model_dump()
    persisted["input_payload"] = {
        "company_name": "历史 IPO",
        "document_text": "历史招股书正文",
        "selected_agent_names": ["financial_agent", "legal_agent"],
    }
    path.write_text(yaml.safe_dump({run.run_id: persisted}, allow_unicode=True))
    monkeypatch.setattr("riskagent.api.analysis._repo", lambda: repo)

    response = TestClient(create_app()).get("/analysis-runs")

    assert response.status_code == 200
    assert response.json()[0]["run_id"] == run.run_id


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


def test_run_debate_stage_emits_structured_failure_event_on_DebateLLMError(
    tmp_path, monkeypatch, analysis_repo_factory
):
    """When debate phase fails entirely, the pipeline degrades gracefully AND emits
    a structured debate_round_failed event carrying phase/failed/total/detail fields.
    """

    def raise_debate_llm_error(**_kwargs):
        raise DebateLLMError(
            phase="arbitration",
            failed=1,
            total=1,
            detail="LLMTransientError: simulated arbiter timeout",
        )

    monkeypatch.setattr(debate_module, "run_debate_phase_sync", raise_debate_llm_error)

    rule_repo = RiskRuleRepository(
        Path(__file__).parents[1] / "configs" / "risk_rules.yaml"
    )
    repo = analysis_repo_factory(
        rule_repository=rule_repo,
        path=tmp_path / "runs.yaml",
    )

    run = repo.create_and_run(
        AnalysisRunCreate(
            company_name="Debate 失败 IPO",
            document_text="公司存在关联交易、赎回安排，经营活动现金流持续为负。",
        )
    )

    # Pipeline should still complete via degradation.
    assert run.status == "completed"

    debate_failures = [
        event
        for event in run.events
        if event.get("event_type") == "debate_round_failed"
    ]
    assert len(debate_failures) == 1
    payload = debate_failures[0].get("payload") or {}
    assert payload["phase"] == "arbitration"
    assert payload["failed"] == 1
    assert payload["total"] == 1
    assert "simulated arbiter timeout" in payload["detail"]
    assert "phase" in payload and "failed" in payload and "total" in payload


def test_completed_run_archives_report_into_knowledge_library(tmp_path, analysis_repo_factory):
    from sqlalchemy import select

    from riskagent.models import ReportLibraryRow

    rule_repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    repo = analysis_repo_factory(
        rule_repository=rule_repo,
        path=tmp_path / "runs.yaml",
    )

    run = repo.create_and_run(
        AnalysisRunCreate(
            company_name="测试 IPO",
            document_text="公司存在关联交易、赎回安排，经营活动现金流持续为负。",
            structured_data={"经营活动现金流": "连续两期为负"},
        )
    )

    assert run.status == "completed"
    assert run.report is not None

    with repo.session_factory() as session:
        row = session.scalar(
            select(ReportLibraryRow).where(ReportLibraryRow.source_run_id == run.run_id)
        )
        assert row is not None
        assert row.report_id == run.report.report_id
        assert row.company_name == "测试 IPO"
        assert row.finding_count == len(run.findings)
        assert row.markdown == run.report.markdown
        assert row.sections and all("title" in item and "text" in item for item in row.sections)

    archive_events = [
        event for event in run.events if event.get("actor_name") == "report_library"
    ]
    assert archive_events and archive_events[0]["event_type"] == "tool_completed"

    # Idempotent: re-archiving the same run must not duplicate rows.
    repo._archive_report(run)
    with repo.session_factory() as session:
        rows = session.scalars(select(ReportLibraryRow)).all()
        assert len(rows) == 1


def test_knowledge_reports_api_lists_and_fetches_archived_reports(monkeypatch, tmp_path):
    import riskagent.api.knowledge as knowledge_module
    from sqlalchemy.orm import sessionmaker

    from riskagent.database import Base, make_engine
    from riskagent.models import ReportLibraryRow

    engine = make_engine(f"sqlite:///{tmp_path / 'knowledge.db'}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(knowledge_module, "SessionLocal", session_factory)
    monkeypatch.setattr(knowledge_module, "init_db", lambda: None)

    with session_factory() as session:
        session.add(
            ReportLibraryRow(
                report_id="report_test123",
                source_run_id="run_test123",
                company_name="测试 IPO",
                document_id="doc_abc",
                rule_set_id="hk_ipo_risk_rules_v1",
                overall_risk_level="中",
                finding_count=6,
                markdown="# 报告\n\n## 财务风险分析\n\n内容",
                sections=[{"title": "财务风险分析", "text": "内容"}],
                created_at="2026-07-14T00:00:00+00:00",
            )
        )
        session.commit()

    client = TestClient(create_app())
    listed = client.get("/knowledge/reports")
    assert listed.status_code == 200
    summaries = listed.json()
    assert len(summaries) == 1
    assert summaries[0]["report_id"] == "report_test123"
    assert summaries[0]["section_count"] == 1
    assert "markdown" not in summaries[0]

    detail = client.get("/knowledge/reports/report_test123")
    assert detail.status_code == 200
    body = detail.json()
    assert body["markdown"].startswith("# 报告")
    assert body["sections"] == [{"title": "财务风险分析", "text": "内容"}]

    assert client.get("/knowledge/reports/missing").status_code == 404
