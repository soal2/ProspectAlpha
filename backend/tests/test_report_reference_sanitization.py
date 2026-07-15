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
    VerificationResult,
)
from riskagent.risk_rules import RiskRuleRepository

from conftest import deterministic_report_provider


def _rule_repository() -> RiskRuleRepository:
    return RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")


def _repository(
    tmp_path: Path,
    analysis_repo_factory,
    *,
    report_provider: Any | None = None,
) -> AnalysisRunRepository:
    repo = analysis_repo_factory(
        rule_repository=_rule_repository(),
        path=tmp_path / "runs.yaml",
    )
    if report_provider is not None:
        repo.report_provider = report_provider
    return repo


def _pipeline_payload() -> AnalysisRunCreate:
    return AnalysisRunCreate(
        company_name="引用清理 IPO",
        document_text="公司存在关联交易、赎回安排，经营活动现金流持续为负。",
        structured_data={"经营活动现金流": "连续两期为负"},
    )


def _debate_result(*, removed_finding_ids: list[str]) -> DebatePhaseResult:
    return DebatePhaseResult(
        critiques=[],
        revisions=[
            {
                "agent_name": "business_agent",
                "original_finding_ids": removed_finding_ids,
                "revised_summary": "删除证据不足 finding。",
                "added_evidence_ids": [],
                "removed_finding_ids": removed_finding_ids,
                "revision_reason": "红队要求移除无效结论。",
            }
        ],
        ruling=ArbiterRuling(
            decision="proceed",
            reasoning="红队完成反馈裁决。",
            required_supplements=[],
            flagged_finding_ids=[],
            approved_finding_ids=[],
        ),
        history={"rounds": [], "ruling": {"decision": "proceed"}},
    )


def _finding(finding_id: str, evidence_id: str, agent_name: str = "business_agent") -> RiskFinding:
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
        confidence=0.6,
        trigger_condition="客户集中度较高",
        evaluation_result="triggered",
        reasoning_summary="客户集中度和回款情况需要进一步核查。",
        evidence_ids=[evidence_id],
        evidence_snippets=["客户集中度较高。"],
        matched_terms=["客户集中", "回款"],
    )


def _evidence(evidence_id: str, finding_id: str) -> RunEvidence:
    return RunEvidence(
        evidence_id=evidence_id,
        finding_id=finding_id,
        document_block_id=f"block-{finding_id}",
        page_number=1,
        section_path="风险因素",
        text="客户集中度较高。",
        bbox=[0, 0, 10, 10],
        source_hash=f"hash-{finding_id}",
        confidence=0.8,
        verifier_status="pending",
    )


def _run_with_module_results(repo: AnalysisRunRepository) -> AnalysisRun:
    run = repo.create_draft(
        AnalysisRunCreate(company_name="辩论引用清理 IPO", document_text="客户集中度较高。")
    )
    run.findings = [
        _finding("f1", "ev-f1", "business_agent"),
        _finding("f2", "ev-f2", "financial_agent"),
    ]
    run.evidence = [_evidence("ev-f1", "f1"), _evidence("ev-f2", "f2")]
    run.module_results = [
        {
            "module_id": "module_business",
            "agent_name": "business_agent",
            "module": "Business specialist",
            "summary": "业务风险摘要。",
            "finding_ids": ["f1", "f2"],
            "evidence_ids": ["ev-f1", "ev-f2"],
        }
    ]
    return run


def test_report_provider_receives_only_admitted_ids(
    tmp_path, monkeypatch, analysis_repo_factory
):
    calls: list[dict[str, Any]] = []

    def probe_report_provider(**kwargs: Any) -> dict[str, Any]:
        calls.append(
            {
                "module_results": kwargs["module_results"],
                "verified_findings": kwargs["verified_findings"],
                "evidence_index": kwargs["evidence_index"],
            }
        )
        return deterministic_report_provider(**kwargs)

    def verify_first_finding_only(
        findings: list[RiskFinding],
        evidence: list[RunEvidence],
    ) -> list[VerificationResult]:
        admitted_id = findings[0].finding_id
        return [
            VerificationResult(
                finding_id=finding.finding_id,
                status="supported"
                if finding.finding_id == admitted_id
                else "needs_human_review",
                checked_evidence_ids=[
                    item.evidence_id
                    for item in evidence
                    if item.finding_id == finding.finding_id
                ],
                reason="test-controlled verification result",
                created_at="2026-07-14T00:00:00+00:00",
            )
            for finding in findings
        ]

    monkeypatch.setattr(
        "riskagent.analysis_runs._verify_findings",
        verify_first_finding_only,
    )
    monkeypatch.setattr(
        AnalysisRunRepository,
        "_run_debate_stage",
        lambda _self, run: run,
    )
    repo = _repository(
        tmp_path,
        analysis_repo_factory,
        report_provider=probe_report_provider,
    )

    run = repo.create_and_run(_pipeline_payload())

    assert run.status == "completed"
    assert calls
    first_call = calls[0]
    admitted_finding_ids = {
        str(item["finding_id"]) for item in first_call["verified_findings"]
    }
    admitted_evidence_ids = {
        str(item["evidence_id"]) for item in first_call["evidence_index"]
    }
    assert admitted_finding_ids
    assert len(admitted_finding_ids) < len(run.findings)
    for module in first_call["module_results"]:
        assert set(map(str, module.get("finding_ids", []))) <= admitted_finding_ids
        assert set(map(str, module.get("evidence_ids", []))) <= admitted_evidence_ids


def test_retry_passes_validation_feedback(tmp_path, analysis_repo_factory):
    calls: list[str | None] = []

    def retrying_report_provider(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs.get("validation_feedback"))
        if len(calls) == 1:
            report = deterministic_report_provider(**kwargs)
            report["sections"][0]["finding_ids"] = ["finding_999"]
            report["sections"][0]["evidence_ids"] = ["ev_999"]
            return report
        return deterministic_report_provider(**kwargs)

    repo = _repository(
        tmp_path,
        analysis_repo_factory,
        report_provider=retrying_report_provider,
    )

    run = repo.create_and_run(_pipeline_payload())

    assert run.report is not None
    assert run.status != "failed"
    assert len(calls) == 2
    assert calls[0] is None
    assert calls[1]
    assert "unknown references" in calls[1]


def test_debate_removal_syncs_module_results_and_evidence(
    tmp_path, monkeypatch, analysis_repo_factory
):
    monkeypatch.setattr(
        debate_module,
        "run_debate_phase_sync",
        lambda **_kwargs: _debate_result(removed_finding_ids=["f1"]),
    )
    repo = _repository(tmp_path, analysis_repo_factory)

    run = repo._run_debate_stage(_run_with_module_results(repo))

    assert {finding.finding_id for finding in run.findings} == {"f2"}
    assert {item.evidence_id for item in run.evidence} == {"ev-f1", "ev-f2"}
    assert next(
        item for item in run.evidence if item.evidence_id == "ev-f1"
    ).verifier_status == "rejected_by_debate"
    for module in run.module_results:
        assert "f1" not in set(map(str, module.get("finding_ids", [])))
        assert "ev-f1" not in set(map(str, module.get("evidence_ids", [])))
