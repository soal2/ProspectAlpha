from __future__ import annotations

from pathlib import Path
from typing import Any

from riskagent import analysis_runs as analysis_runs_module
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
from riskagent.retrieval import ChunkSearchResult


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
            relevance_score=0.8,
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
            relevance_score=0.75,
            verifier_status="pending",
        ),
    ]
    return run


def test_retrieval_candidates_remain_visible_without_becoming_formal_evidence(
    tmp_path, analysis_repo_factory
):
    repo = _repository(tmp_path, analysis_repo_factory)
    run = repo.create_draft(
        AnalysisRunCreate(company_name="候选证据测试", document_text="关联交易")
    )
    run.events = [
        {
            "event_type": "retrieval_query_completed",
            "payload": {
                "chunks": [
                    {
                        "chunk_id": "chunk-related-party",
                        "document_id": "doc-test",
                        "page_number": 576,
                        "section_path": "关联方交易",
                        "text": "本集团与关联方发生以下交易。",
                        "bbox": [10, 20, 30, 40],
                        "bbox_coordinate_space": "normalized_1000",
                        "retrieval_score": 0.82,
                    }
                ]
            },
        }
    ]

    candidates = repo._recover_retrieval_candidates(run)

    assert len(candidates) == 1
    assert candidates[0].verifier_status == "retrieval_candidate"
    assert candidates[0].retrieval_score == 0.82
    assert candidates[0].relevance_score == 0.0
    assert candidates[0].page_number == 576
    assert candidates[0].finding_id == "retrieval_candidate"


def test_legacy_retrieval_chunk_ids_are_backfilled_from_postgres(
    tmp_path, monkeypatch, analysis_repo_factory
):
    repo = _repository(tmp_path, analysis_repo_factory)
    repo.use_database = True
    run = repo.create_draft(
        AnalysisRunCreate(company_name="历史候选测试", document_text="供应商集中")
    )
    run.events = [
        {
            "event_type": "retrieval_query_completed",
            "payload": {
                "chunk_ids": ["doc_legacy_chunk_000007"],
            },
        }
    ]
    chunk = ChunkSearchResult(
        chunk_id="doc_legacy_chunk_000007",
        document_id="doc_legacy",
        doc_key="legacy",
        source_file="legacy_content_list.json",
        source_item_indices=[7],
        chunk_type="table",
        text="前五大供应商采购占比为 82%。",
        page_idx=88,
        score=0.0,
        bbox_refs=[],
        merged_bboxes=[{"page_idx": 88, "bbox": [1, 2, 3, 4]}],
        image_path=None,
        mineru_img_path=None,
        table_html=None,
        metadata={
            "section_path": "主要供应商",
            "bbox_coordinate_space": "normalized_1000",
        },
    )

    class FakeIndexer:
        def __init__(self, ensure=False):
            assert ensure is False

        def list_document_chunks(self, document_id):
            assert document_id == "doc_legacy"
            return [chunk]

        def close(self):
            return None

    monkeypatch.setattr(analysis_runs_module, "PostgresChunkIndexer", FakeIndexer)

    candidates = repo._recover_retrieval_candidates(run)

    assert len(candidates) == 1
    assert candidates[0].document_id == "doc_legacy"
    assert candidates[0].page_number == 88
    assert candidates[0].retrieval_score == 0.0
    assert candidates[0].verifier_status == "retrieval_candidate"


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
    rejected = next(item for item in run.evidence if item.evidence_id == "ev-f1")
    assert rejected.verifier_status == "rejected_by_debate"


def test_legacy_rejected_evidence_is_recovered_only_from_real_chunks(
    tmp_path, monkeypatch, analysis_repo_factory
):
    repo = _repository(tmp_path, analysis_repo_factory)
    repo.use_database = True
    run = _run_with_findings(repo)
    run.evidence = []
    run.events = [
        {
            "event_type": "evidence_found",
            "payload": {
                "evidence_id": "ev-legacy",
                "finding_id": "f-removed",
                "document_id": "doc-real",
                "document_block_id": "chunk-real",
                "page_number": 7,
                "bbox": [100, 200, 300, 400],
                "confidence": 0.71,
            },
        },
        {
            "event_type": "evidence_found",
            "payload": {
                "evidence_id": "ev-unmatched",
                "document_id": "doc-real",
                "document_block_id": "missing",
                "page_number": 99,
                "bbox": [1, 2, 3, 4],
            },
        },
    ]

    chunk = ChunkSearchResult(
        chunk_id="chunk-real",
        document_id="doc-real",
        doc_key="real",
        chunk_type="text",
        text="真实 PostgreSQL 原文证据。",
        page_idx=7,
        source_file="real.pdf",
        source_item_indices=[1],
        bbox_refs=[],
        merged_bboxes=[{"page_idx": 7, "bbox": [100, 200, 300, 400]}],
        image_path=None,
        mineru_img_path=None,
        table_html=None,
        metadata={
            "section_path": "风险因素",
            "bbox_coordinate_space": "normalized_1000",
        },
    )

    class FakeIndexer:
        def list_document_chunks(self, document_id):
            assert document_id == "doc-real"
            return [chunk]

        def close(self):
            return None

    monkeypatch.setattr(analysis_runs_module, "PostgresChunkIndexer", FakeIndexer)

    recovered = repo._recover_rejected_evidence(run)

    assert [item.evidence_id for item in recovered] == ["ev-legacy"]
    assert recovered[0].document_block_id == "chunk-real"
    assert recovered[0].bbox_coordinate_space == "normalized_1000"
    assert recovered[0].verifier_status == "rejected_by_debate"


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
