from __future__ import annotations

from pathlib import Path
from typing import Any

from riskagent.agents import debate as debate_module
from riskagent.agents.debate import DebatePhaseResult
from riskagent.agents.orchestrator import ArbiterRuling
from riskagent.analysis_runs import (
    AnalysisRun,
    AnalysisRunCreate,
    AnalysisRunRepository,
    RiskFinding,
    RunEvidence,
)
from riskagent.risk_rules import RiskRuleRepository


def _repository(
    tmp_path: Path,
    analysis_repo_factory,
) -> AnalysisRunRepository:
    return analysis_repo_factory(
        rule_repository=RiskRuleRepository(
            Path(__file__).parents[1] / "configs" / "risk_rules.yaml"
        ),
        path=tmp_path / "runs.yaml",
    )


def _finding(finding_id: str, agent_name: str, confidence: float = 0.5) -> RiskFinding:
    return RiskFinding(
        finding_id=finding_id,
        agent_name=agent_name,
        rule_set_id="hk_ipo_prospectus_risk_framework_v1",
        rule_version="v1",
        rule_id=f"HKIPO-{finding_id}",
        dimension_code="business",
        rule_type="qualitative",
        category="客户集中",
        title=f"{finding_id} 风险",
        severity="medium",
        confidence=confidence,
        trigger_condition="客户集中度较高",
        evaluation_result="triggered",
        reasoning_summary="客户集中度和回款情况需要进一步核查。",
        evidence_ids=[f"ev-{finding_id}"],
        evidence_snippets=["客户集中度较高。"],
        matched_terms=["客户集中", "回款"],
    )


def _run_with_findings(repo: AnalysisRunRepository) -> AnalysisRun:
    run = repo.create_draft(
        AnalysisRunCreate(company_name="反馈测试 IPO", document_text="客户集中度较高。")
    )
    run.findings = [
        _finding("f1", "business_agent", 0.6),
        _finding("f2", "financial_agent", 0.5),
    ]
    run.evidence = [
        RunEvidence(
            evidence_id="ev-f1",
            finding_id="f1",
            document_block_id="block-f1",
            page_number=1,
            section_path="风险因素",
            text="客户集中度较高。",
            bbox=[0, 0, 10, 10],
            source_hash="hash-f1",
            confidence=0.8,
            verifier_status="pending",
        ),
        RunEvidence(
            evidence_id="ev-f2",
            finding_id="f2",
            document_block_id="block-f2",
            page_number=2,
            section_path="财务资料",
            text="回款情况波动。",
            bbox=[0, 0, 10, 10],
            source_hash="hash-f2",
            confidence=0.75,
            verifier_status="pending",
        ),
    ]
    return run


def _debate_result(
    *,
    revisions: list[dict[str, Any]] | None = None,
    decision: str = "proceed",
    required_supplements: list[Any] | None = None,
    flagged_finding_ids: list[str] | None = None,
    approved_finding_ids: list[str] | None = None,
) -> DebatePhaseResult:
    return DebatePhaseResult(
        critiques=[],
        revisions=revisions or [],
        ruling=ArbiterRuling(
            decision=decision,
            reasoning="红队完成反馈裁决。",
            required_supplements=required_supplements or [],
            flagged_finding_ids=flagged_finding_ids or [],
            approved_finding_ids=approved_finding_ids or [],
        ),
        history={"rounds": [], "ruling": {"decision": decision}},
    )


def test_removed_finding_ids_actually_removed(tmp_path, monkeypatch, analysis_repo_factory):
    monkeypatch.setattr(
        debate_module,
        "run_debate_phase_sync",
        lambda **_kwargs: _debate_result(
            revisions=[
                {
                    "agent_name": "business_agent",
                    "original_finding_ids": ["f1"],
                    "revised_summary": "删除无效 finding。",
                    "added_evidence_ids": [],
                    "removed_finding_ids": ["f1"],
                    "revision_reason": "证据不足。",
                }
            ]
        ),
    )
    repo = _repository(tmp_path, analysis_repo_factory)
    run = repo._run_debate_stage(_run_with_findings(repo))

    assert [finding.finding_id for finding in run.findings] == ["f2"]


def test_approved_finding_ids_confidence_boosted(tmp_path, monkeypatch, analysis_repo_factory):
    monkeypatch.setattr(
        debate_module,
        "run_debate_phase_sync",
        lambda **_kwargs: _debate_result(approved_finding_ids=["f2"]),
    )
    repo = _repository(tmp_path, analysis_repo_factory)
    run = repo._run_debate_stage(_run_with_findings(repo))

    f2 = next(finding for finding in run.findings if finding.finding_id == "f2")
    assert f2.confidence >= 0.7
    assert f2.verification_status == "approved"


def test_request_supplement_triggers_supplement_loop(
    tmp_path, monkeypatch, analysis_repo_factory
):
    monkeypatch.setattr(
        debate_module,
        "run_debate_phase_sync",
        lambda **_kwargs: _debate_result(
            decision="request_supplement",
            required_supplements=["补充回款风险原文证据"],
        ),
    )
    repo = _repository(tmp_path, analysis_repo_factory)
    run = repo._run_debate_stage(_run_with_findings(repo))

    # A supplement without a document_id cannot query the document-scoped
    # PostgreSQL index, and must not fall back to the legacy run index.
    assert len(run.evidence) == 2


def test_escalate_human_pauses_pipeline(tmp_path, monkeypatch, analysis_repo_factory):
    monkeypatch.setattr(
        debate_module,
        "run_debate_phase_sync",
        lambda **_kwargs: _debate_result(
            decision="escalate_human",
            flagged_finding_ids=["f1"],
        ),
    )
    repo = _repository(tmp_path, analysis_repo_factory)
    run = repo._run_debate_stage(_run_with_findings(repo))

    assert run.status == "awaiting_human_review"
    assert run.current_stage == "expert_debate"
    assert any(event["event_type"] == "run_paused_for_human" for event in run.events)
    assert not any(event.get("stage_key") == "cross_validation" for event in run.events)
