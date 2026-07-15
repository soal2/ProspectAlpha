from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from riskagent.agents import debate as debate_module
from riskagent.agents.debate import DebatePhaseResult
from riskagent.agents.orchestrator import ArbiterRuling, StrategyAssignment
from riskagent.agents.supervisor import AgentTeamSupervisor
from riskagent.agent_experts import AgentSpec
from riskagent.a2a import MessageType
from riskagent.analysis_runs import (
    AnalysisRun,
    AnalysisRunCreate,
    AnalysisRunRepository,
    RiskFinding,
    RunEvidence,
    VerificationResult,
    _build_report_streaming,
)
from riskagent.risk_rules import RiskRuleRepository
from riskagent.runtime_events import EventBroker


def _repository(tmp_path: Path, analysis_repo_factory) -> AnalysisRunRepository:
    return analysis_repo_factory(
        rule_repository=RiskRuleRepository(
            Path(__file__).parents[1] / "configs" / "risk_rules.yaml"
        ),
        path=tmp_path / "runs.yaml",
    )


def _run_with_finding(repo: AnalysisRunRepository):
    run = repo.create_draft(
        AnalysisRunCreate(company_name="中间事件 IPO", document_text="公司存在关联交易。")
    )
    run.findings.append(
        RiskFinding(
            finding_id="finding-1",
            agent_name="legal_agent",
            rule_set_id="hk_ipo_prospectus_risk_framework_v1",
            rule_version="v1",
            rule_id="HKIPO-TEST-001",
            dimension_code="legal",
            rule_type="qualitative",
            category="关联交易",
            title="关联交易风险",
            severity="medium",
            confidence=0.8,
            trigger_condition="披露关联交易",
            evaluation_result="triggered",
            reasoning_summary="招股书披露关联交易，需要进一步核查定价公允性。",
            evidence_ids=["evidence-1"],
            evidence_snippets=["公司存在关联交易。"],
            matched_terms=["关联交易"],
        )
    )
    run.module_results = [
        {
            "module_id": "module_legal",
            "agent_name": "legal_agent",
            "finding_ids": ["finding-1"],
        }
    ]
    return run


def _debate_result() -> DebatePhaseResult:
    return DebatePhaseResult(
        critiques=[
            {
                "critic_agent": "financial_agent",
                "target_agent": "legal_agent",
                "finding_ids": ["finding-1"],
                "critique_summary": "需要补充交易金额和定价依据。",
                "suggested_revisions": ["补充交易金额"],
                "severity": "minor",
            }
        ],
        revisions=[
            {
                "agent_name": "legal_agent",
                "original_finding_ids": ["finding-1"],
                "revised_summary": "已补充关联交易核查关注点。",
                "added_evidence_ids": [],
                "removed_finding_ids": [],
                "revision_reason": "回应同侪质证。",
            }
        ],
        ruling=ArbiterRuling(
            decision="proceed",
            reasoning="证据链满足进入交叉验证的最低要求。",
            required_supplements=[],
            flagged_finding_ids=[],
            approved_finding_ids=["finding-1"],
        ),
        history={"rounds": [], "ruling": {"decision": "proceed"}},
    )


def test_publish_event_persists_and_pushes_to_broker(
    tmp_path, monkeypatch, analysis_repo_factory
):
    import riskagent.runtime_events as runtime_events

    broker = EventBroker()
    monkeypatch.setattr(runtime_events, "get_event_broker", lambda: broker)
    repo = _repository(tmp_path, analysis_repo_factory)
    run = repo.create_draft(
        AnalysisRunCreate(company_name="Broker IPO", document_text="招股书正文")
    )
    queue = asyncio.run(broker.subscribe(run.run_id))
    event_count = len(run.events)
    event = {
        "event_type": "test_event",
        "actor_name": "orchestrator",
        "stage_key": "expert_debate",
        "payload": {"phase": "debate"},
    }

    repo._publish_event(run, event)

    assert queue.get_nowait()["event_type"] == event["event_type"]
    assert len(run.events) == event_count + 1
    assert run.events[-1]["event_id"]


def test_run_debate_stage_emits_llm_call_started_and_completed(
    tmp_path, monkeypatch, analysis_repo_factory
):
    import riskagent.runtime_events as runtime_events

    captured: list[dict[str, Any]] = []
    broker = EventBroker()

    def publish_spy(run_id: str, event: dict[str, Any]) -> None:
        captured.append(event)

    monkeypatch.setattr(runtime_events, "get_event_broker", lambda: broker)
    monkeypatch.setattr(broker, "publish_sync", publish_spy)
    monkeypatch.setattr(
        debate_module,
        "run_debate_phase_sync",
        lambda **_kwargs: _debate_result(),
    )
    repo = _repository(tmp_path, analysis_repo_factory)
    run = _run_with_finding(repo)

    repo._run_debate_stage(run)

    event_types = [event["event_type"] for event in captured]
    assert "llm_call_started" in event_types
    assert "llm_call_completed" in event_types
    started = next(event for event in captured if event["event_type"] == "llm_call_started")
    completed = next(event for event in captured if event["event_type"] == "llm_call_completed")
    assert started["actor_name"] == "orchestrator"
    assert started["stage_key"] == "expert_debate"
    assert started["payload"] == {"phase": "debate", "finding_count": 1}
    assert completed["actor_name"] == "orchestrator"
    assert completed["stage_key"] == "expert_debate"
    assert completed["payload"] == {
        "phase": "debate",
        "critique_count": 1,
        "revision_count": 1,
        "decision": "proceed",
    }


def test_run_debate_stage_emits_llm_call_failed_on_exception(
    tmp_path, monkeypatch, analysis_repo_factory
):
    import riskagent.runtime_events as runtime_events

    captured: list[dict[str, Any]] = []
    broker = EventBroker()

    def publish_spy(run_id: str, event: dict[str, Any]) -> None:
        captured.append(event)

    def raise_runtime_error(**_kwargs):
        raise RuntimeError("simulated debate failure")

    monkeypatch.setattr(runtime_events, "get_event_broker", lambda: broker)
    monkeypatch.setattr(broker, "publish_sync", publish_spy)
    monkeypatch.setattr(debate_module, "run_debate_phase_sync", raise_runtime_error)
    repo = _repository(tmp_path, analysis_repo_factory)
    run = _run_with_finding(repo)

    repo._run_debate_stage(run)

    event_types = [event["event_type"] for event in captured]
    assert "llm_call_started" in event_types
    assert "llm_call_failed" in event_types
    failed = next(event for event in captured if event["event_type"] == "llm_call_failed")
    assert failed["actor_name"] == "orchestrator"
    assert failed["stage_key"] == "expert_debate"
    assert failed["payload"] == {"phase": "debate", "error": "simulated debate failure"}


class _StreamingLLM:
    async def chat_stream(self, *_args: Any, **_kwargs: Any):
        yield "LLM 正文"


class _PeerContext:
    message_type = MessageType.TASK
    available_peers: list[dict[str, Any]] = []


def _report_run() -> AnalysisRun:
    return AnalysisRun(
        run_id="run_report_intermediate",
        company_name="报告测试 IPO",
        status="completed",
        current_stage="report_generation",
        created_at="2026-07-13T00:00:00Z",
        completed_at="2026-07-13T00:01:00Z",
        data_cutoff_date="2026-07-13",
        rule_snapshot={"rule_set_id": "rs-1", "version": "v1"},
        analysis_config={},
        input_summary={},
        findings=[
            RiskFinding(
                finding_id="finding-financial",
                agent_name="financial_agent",
                skill_id="financial_risk_skill",
                rule_set_id="rs-1",
                rule_version="v1",
                rule_id="R-FIN",
                dimension_code="financial",
                rule_type="quantitative_financial",
                category="经营风险",
                title="现金流风险",
                severity="high",
                confidence=0.9,
                trigger_condition="现金流为负",
                evaluation_result="triggered",
                reasoning_summary="现金流承压。",
                evidence_ids=["evidence-financial"],
                evidence_snippets=["经营活动现金流为负。"],
                matched_terms=["现金流"],
            ),
            RiskFinding(
                finding_id="finding-legal",
                agent_name="legal_agent",
                skill_id="legal_compliance_skill",
                rule_set_id="rs-1",
                rule_version="v1",
                rule_id="R-LEGAL",
                dimension_code="legal",
                rule_type="qualitative",
                category="关联交易",
                title="关联交易风险",
                severity="medium",
                confidence=0.85,
                trigger_condition="关联交易",
                evaluation_result="triggered",
                reasoning_summary="关联交易需核查。",
                evidence_ids=["evidence-legal"],
                evidence_snippets=["存在关联交易。"],
                matched_terms=["关联交易"],
            ),
        ],
        evidence=[
            RunEvidence(
                evidence_id="evidence-financial",
                finding_id="finding-financial",
                document_block_id="block-financial",
                page_number=12,
                section_path="风险因素/财务风险",
                text="经营活动现金流为负。",
                bbox=[0, 0, 10, 10],
                source_hash="hash-financial",
                relevance_score=0.86,
                verifier_status="supported",
            ),
            RunEvidence(
                evidence_id="evidence-legal",
                finding_id="finding-legal",
                document_block_id="block-legal",
                page_number=18,
                section_path="风险因素/合规风险",
                text="存在关联交易。",
                bbox=[0, 0, 10, 10],
                source_hash="hash-legal",
                relevance_score=0.82,
                verifier_status="supported",
            ),
        ],
        verification_results=[
            VerificationResult(
                finding_id="finding-financial",
                status="supported",
                checked_evidence_ids=["evidence-financial"],
                reason="证据充分",
                created_at="2026-07-13T00:00:10Z",
            ),
            VerificationResult(
                finding_id="finding-legal",
                status="weakly_supported",
                checked_evidence_ids=["evidence-legal"],
                reason="弱支持",
                created_at="2026-07-13T00:00:11Z",
            ),
        ],
    )


async def _collect_report(run: AnalysisRun) -> str:
    return "".join([chunk async for chunk in _build_report_streaming(run)])


def test_build_report_streaming_emits_started_and_completed_for_each_llm_section(
    monkeypatch,
):
    import riskagent.analysis_runs as analysis_runs_module
    import riskagent.runtime_events as runtime_events

    captured: list[dict[str, Any]] = []
    broker = EventBroker()

    async def publish_spy(run_id: str, event: dict[str, Any]) -> None:
        captured.append(event)

    monkeypatch.setattr(runtime_events, "get_event_broker", lambda: broker)
    monkeypatch.setattr(broker, "publish", publish_spy)
    monkeypatch.setattr(analysis_runs_module, "get_default_llm_client", lambda: _StreamingLLM())

    body = asyncio.run(_collect_report(_report_run()))

    assert "LLM 正文" in body
    event_types = [event["event_type"] for event in captured]
    assert event_types == [
        "report_section_started",
        "report_section_completed",
        "report_section_started",
        "report_section_completed",
    ]


def test_expert_handler_emits_llm_second_pass_started_and_completed(
    tmp_path, monkeypatch
):
    import riskagent.agents.supervisor as supervisor_module
    import riskagent.runtime_events as runtime_events

    captured: list[dict[str, Any]] = []
    broker = EventBroker()

    def publish_spy(run_id: str, event: dict[str, Any]) -> None:
        captured.append(event)

    async def enrich_candidates(**kwargs: Any):
        for candidate in kwargs["candidates"]:
            candidate.reasoning_summary = "LLM 二判完成。"
        return []

    monkeypatch.setattr(runtime_events, "get_event_broker", lambda: broker)
    monkeypatch.setattr(broker, "publish_sync", publish_spy)
    monkeypatch.setattr(supervisor_module, "analyze_findings_with_llm", enrich_candidates)
    repository = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    spec = AgentSpec(
        name="related_party_agent",
        title="Related party specialist",
        categories=("关联交易",),
    )
    assignment = StrategyAssignment(
        module_id="related_party",
        agent_name=spec.name,
        module="关联交易风险",
        goal="Analyze related party transactions",
        expected_outputs=["RiskFinding"],
    )
    supervisor = AgentTeamSupervisor(
        repository=repository,
        agent_specs=[spec],
        runtime_root=tmp_path / "agent-runtime",
    )
    handler = supervisor._expert_handler(
        run_id="run_expert_intermediate",
        spec=spec,
        assignment=assignment,
        text="公司存在关联交易及持续采购安排。",
        rules=repository.select_rules(),
        rule_snapshot=repository.load().model_dump(),
    )

    result = handler({"assignment": assignment.model_dump()}, _PeerContext())

    assert result["evaluations"]
    event_types = [event["event_type"] for event in captured]
    assert "llm_response_started" in event_types
    assert "expert_llm_started" in event_types
    assert "expert_llm_completed" in event_types
