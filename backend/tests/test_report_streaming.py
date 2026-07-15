from __future__ import annotations

import asyncio

from riskagent import analysis_runs as analysis_runs_module
from riskagent.analysis_runs import (
    AnalysisRun,
    DocumentBlock,
    RiskFinding,
    RunEvidence,
    VerificationResult,
    _build_report,
    _build_report_streaming,
    _render_report_header,
)


class _StreamingLLM:
    def __init__(self, chunks: list[str] | Exception):
        self.chunks = chunks
        self.calls: list[list[dict[str, str]]] = []

    async def chat_stream(self, messages, *, temperature: float = 0.4):
        self.calls.append(messages)
        if isinstance(self.chunks, Exception):
            raise self.chunks
        for chunk in self.chunks:
            yield chunk


def _finding(
    finding_id: str,
    *,
    title: str,
    skill_id: str | None,
    rule_type: str = "rule",
    category: str = "经营风险",
    evidence_id: str,
) -> RiskFinding:
    return RiskFinding(
        finding_id=finding_id,
        agent_name="test_agent",
        skill_id=skill_id,
        rule_set_id="rs-1",
        rule_version="v1",
        rule_id=f"R-{finding_id}",
        dimension_code="dim",
        rule_type=rule_type,
        category=category,
        title=title,
        severity="high",
        confidence=0.9,
        trigger_condition="触发条件",
        evaluation_result="triggered",
        reasoning_summary=f"{title} 摘要",
        evidence_ids=[evidence_id],
        evidence_snippets=["证据片段"],
        matched_terms=["风险"],
    )


def _evidence(evidence_id: str, finding_id: str, page_number: int) -> RunEvidence:
    return RunEvidence(
        evidence_id=evidence_id,
        finding_id=finding_id,
        document_block_id=f"block-{evidence_id}",
        page_number=page_number,
        section_path="风险因素/财务风险",
        text=f"{finding_id} 原文证据",
        bbox=[0, 0, 10, 10],
        source_hash=f"hash-{evidence_id}",
        confidence=0.86,
        verifier_status="supported",
    )


def _run() -> AnalysisRun:
    findings = [
        _finding(
            "f1",
            title="现金流承压",
            skill_id="financial_risk_skill",
            rule_type="quantitative_financial",
            evidence_id="e1",
        ),
        _finding(
            "f2",
            title="合规处罚",
            skill_id="legal_compliance_skill",
            category="政策监管",
            evidence_id="e2",
        ),
    ]
    return AnalysisRun(
        run_id="run_report",
        company_name="测试科技",
        status="completed",
        current_stage="completed",
        created_at="2026-07-13T00:00:00Z",
        completed_at="2026-07-13T00:01:00Z",
        data_cutoff_date="2026-07-13",
        rule_snapshot={"rule_set_id": "rs-1", "version": "v1"},
        analysis_config={},
        input_summary={},
        document_blocks=[
            DocumentBlock(
                block_id="block-e1",
                document_id="doc-1",
                page_number=12,
                section_path="风险因素/财务风险",
                text="经营活动现金流为负。",
                bbox=[0, 0, 10, 10],
                source_hash="hash-e1",
            )
        ],
        findings=findings,
        evidence=[_evidence("e1", "f1", 12), _evidence("e2", "f2", 18)],
        verification_results=[
            VerificationResult(
                finding_id="f1",
                status="supported",
                checked_evidence_ids=["e1"],
                reason="证据充分",
                created_at="2026-07-13T00:00:10Z",
            ),
            VerificationResult(
                finding_id="f2",
                status="weakly_supported",
                checked_evidence_ids=["e2"],
                reason="弱支持",
                created_at="2026-07-13T00:00:11Z",
            ),
        ],
    )


async def _collect(run: AnalysisRun) -> list[str]:
    return [chunk async for chunk in _build_report_streaming(run)]


def test_render_report_header_includes_company_and_cutoff_date():
    header = _render_report_header(_run())

    assert "测试科技 IPO 风险分析草案" in header
    assert "数据截止日期：2026-07-13" in header


def test_build_report_streaming_yields_multiple_chunks_with_mock_llm(monkeypatch):
    llm = _StreamingLLM(["LLM段落A", "LLM段落B"])
    monkeypatch.setattr(analysis_runs_module, "get_default_llm_client", lambda: llm)

    chunks = asyncio.run(_collect(_run()))
    body = "".join(chunks)

    assert len(chunks) > 5
    assert "## 财务风险分析" in body
    assert "## 合规风险分析" in body
    assert "LLM段落A" in body
    assert "LLM段落B" in body
    assert len(llm.calls) == 2


def test_build_report_streaming_degrades_when_llm_fails(monkeypatch):
    llm = _StreamingLLM(RuntimeError("boom"))
    monkeypatch.setattr(analysis_runs_module, "get_default_llm_client", lambda: llm)

    body = "".join(asyncio.run(_collect(_run())))

    assert "LLM 流式生成失败：RuntimeError: boom" in body
    assert "已降级为模板输出" in body
    assert "- 现金流承压：现金流承压 摘要（证据：第 12 页，风险因素/财务风险）" in body


def test_build_report_sync_wrapper_returns_report_draft(monkeypatch):
    llm = _StreamingLLM(["同步包装正文"])
    monkeypatch.setattr(analysis_runs_module, "get_default_llm_client", lambda: llm)

    draft = _build_report(_run())

    assert draft.report_id.startswith("report_")
    assert draft.status == "generated"
    assert "同步包装正文" in draft.markdown
    assert draft.html == draft.markdown.replace("\n", "<br>")
