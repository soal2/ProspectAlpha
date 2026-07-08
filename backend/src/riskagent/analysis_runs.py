"""Local MVP analysis-run service for source-grounded IPO risk checks."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from sqlalchemy import select

from riskagent.agents.orchestrator import orchestrate
from riskagent.agents.supervisor import AgentTeamSupervisor
from riskagent.common.cache import TTLCache
from riskagent.database import SessionLocal, init_db
from riskagent.documents import (
    DocumentBlock as ParsedDocumentBlock,
    DocumentRepository,
    get_default_document_repository,
)
from riskagent.risk_rules import RiskRuleRepository, get_default_repository
from riskagent.skills.risk_extract import RuleEvaluation
from riskagent.models import AnalysisRunRow, EvidenceLibraryRow
from riskagent.retrieval.hybrid import HybridSearchIndex
from riskagent.workers.analysis import dispatch_analysis_job


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANALYSIS_RUNS_PATH = BACKEND_ROOT / "data" / "processed" / "analysis_runs.yaml"
DEFAULT_EVIDENCE_LIBRARY_PATH = BACKEND_ROOT / "data" / "processed" / "evidence_library.yaml"
PARSER_VERSION = "local-text-parser-v1"


class AnalysisRunCreate(BaseModel):
    company_name: str = "待确认公司"
    document_text: str = ""
    document_id: str | None = None
    material_url: str | None = None
    ipo_project_hint: str | None = None
    confirm_defaults: bool = True
    risk_rule_set_id: str | None = None
    selected_agent_names: list[str] = Field(default_factory=list)
    analysis_depth: Literal["quick", "standard", "deep"] = "standard"
    structured_data: dict[str, Any] = Field(default_factory=dict)
    max_findings: int = 30


class AnalysisRunInputPatch(BaseModel):
    company_name: str | None = None
    document_text: str | None = None
    document_id: str | None = None
    material_url: str | None = None
    ipo_project_hint: str | None = None
    confirm_defaults: bool | None = None
    risk_rule_set_id: str | None = None
    selected_agent_names: list[str] | None = None
    analysis_depth: Literal["quick", "standard", "deep"] | None = None
    structured_data: dict[str, Any] | None = None
    max_findings: int | None = None


class DocumentBlock(BaseModel):
    block_id: str
    document_id: str
    page_number: int
    section_path: str
    block_type: str = "text"
    text: str
    bbox: list[float]
    char_start: int = 0
    char_end: int = 0
    source_hash: str
    parser_version: str = PARSER_VERSION


class RunEvidence(BaseModel):
    evidence_id: str
    finding_id: str
    document_block_id: str
    page_number: int
    section_path: str
    text: str
    bbox: list[float] = Field(default_factory=list)
    char_start: int = 0
    char_end: int = 0
    source_hash: str = ""
    confidence: float
    verifier_status: str


class VerificationResult(BaseModel):
    finding_id: str
    status: str
    checked_evidence_ids: list[str]
    reason: str
    created_at: str


class RiskFinding(BaseModel):
    finding_id: str
    agent_name: str = "rule_agent"
    rule_set_id: str
    rule_version: str
    rule_id: str
    dimension_code: str
    rule_type: str
    category: str
    title: str
    severity: str
    confidence: float
    trigger_condition: str
    evaluation_result: str
    reasoning_summary: str
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_snippets: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


class ReportDraft(BaseModel):
    report_id: str
    status: str = "generated"
    review_status: str = "draft"
    markdown: str
    html: str
    created_at: str


class TraceStage(BaseModel):
    key: str
    label: str
    owner: str
    status: str
    cost: str | None = None


class TraceEvent(BaseModel):
    who: str
    kind: str
    text: str
    t: str


class TraceEvidenceItem(BaseModel):
    id: str
    source: str
    title: str
    conf: float
    time: str


class AnalysisTrace(BaseModel):
    stages: list[TraceStage]
    seed: list[TraceEvent]
    flow: list[TraceEvent]
    evidenceFeed: list[TraceEvidenceItem]


class AnalysisRun(BaseModel):
    run_id: str
    company_name: str
    status: str
    current_stage: str
    created_at: str
    completed_at: str | None = None
    data_cutoff_date: str
    rule_snapshot: dict[str, Any]
    analysis_config: dict[str, Any] = Field(default_factory=dict)
    input_summary: dict[str, Any]
    understood: dict[str, Any] = Field(default_factory=dict)
    blocking_questions: list[str] = Field(default_factory=list)
    optional_questions: list[str] = Field(default_factory=list)
    assumed_defaults: dict[str, str] = Field(default_factory=dict)
    inputs_frozen: bool = False
    orchestra_plan: dict[str, Any] = Field(default_factory=dict)
    frozen_rule_snapshot: dict[str, Any] = Field(default_factory=dict, exclude=True)
    document_blocks: list[DocumentBlock] = Field(default_factory=list, exclude=True)
    findings: list[RiskFinding] = Field(default_factory=list)
    evidence: list[RunEvidence] = Field(default_factory=list, exclude=True)
    verification_results: list[VerificationResult] = Field(default_factory=list, exclude=True)
    report: ReportDraft | None = Field(default=None, exclude=True)
    events: list[dict[str, Any]] = Field(default_factory=list)
    input_payload: AnalysisRunCreate | None = Field(default=None, exclude=True)


class AnalysisRunRepository:
    def __init__(
        self,
        path: Path | str | None = None,
        rule_repository: RiskRuleRepository | None = None,
        evidence_library_path: Path | str | None = None,
        document_repository: DocumentRepository | None = None,
        session_factory=SessionLocal,
        use_database: bool | None = None,
        search_index: HybridSearchIndex | None = None,
        agent_supervisor: AgentTeamSupervisor | None = None,
    ) -> None:
        configured = path or os.getenv("ANALYSIS_RUNS_PATH") or DEFAULT_ANALYSIS_RUNS_PATH
        self.path = Path(configured)
        self.rule_repository = rule_repository or get_default_repository()
        self.evidence_library_path = Path(
            evidence_library_path
            or os.getenv("EVIDENCE_LIBRARY_PATH")
            or DEFAULT_EVIDENCE_LIBRARY_PATH
        )
        self.document_repository = document_repository or get_default_document_repository()
        self.session_factory = session_factory
        self.use_database = use_database if use_database is not None else os.getenv("USE_DATABASE", "1") != "0"
        if self.use_database:
            init_db()
        self.search_cache: TTLCache[list[dict[str, Any]]] = TTLCache(ttl_seconds=30, max_items=256)
        self.search_index = search_index or HybridSearchIndex(session_factory=session_factory)
        self.agent_supervisor = agent_supervisor or AgentTeamSupervisor(repository=self.rule_repository)

    def create_draft(self, payload: AnalysisRunCreate) -> AnalysisRun:
        now = _now()
        rule_snapshot = self.rule_repository.snapshot()
        _validate_rule_set(payload, rule_snapshot)
        questions = _blocking_questions(payload)
        status = "ready" if not questions else "clarifying"
        run = AnalysisRun(
            run_id=f"run_{uuid.uuid4().hex[:12]}",
            company_name=payload.company_name,
            status=status,
            current_stage=status,
            created_at=now,
            data_cutoff_date=date.today().isoformat(),
            rule_snapshot=_compact_rule_snapshot(rule_snapshot),
            analysis_config=_analysis_config(payload, self.agent_supervisor.agent_options()),
            frozen_rule_snapshot=rule_snapshot,
            input_summary=_input_summary(payload),
            understood=_understood(payload),
            blocking_questions=questions,
            optional_questions=["可补充行业、保荐人、发行规模或拟上市代码以提高报告解释力度。"],
            assumed_defaults={
                "risk_rule_version": payload.risk_rule_set_id or rule_snapshot["rule_set_id"],
                "analysis_depth": payload.analysis_depth,
                "agent_group": "selected" if payload.selected_agent_names else "default",
            },
            events=[
                _event("stage_started", "analysis_run", "创建分析任务", "已完成信息完整性检查。"),
            ],
            input_payload=payload,
        )
        self._save_run(run)
        return run

    def create_and_run(self, payload: AnalysisRunCreate) -> AnalysisRun:
        run = self.create_draft(payload)
        if run.status != "ready":
            return run
        return self.start(run.run_id)

    def update_inputs(self, run_id: str, patch: AnalysisRunInputPatch) -> AnalysisRun:
        run = self.get(run_id)
        if run.inputs_frozen:
            raise ValueError("Analysis inputs are frozen after start.")
        payload = _payload_from_run(run)
        patched = payload.model_copy(update=patch.model_dump(exclude_unset=True))
        questions = _blocking_questions(patched)
        run.company_name = patched.company_name
        run.status = "ready" if not questions else "clarifying"
        run.current_stage = run.status
        run.input_summary = _input_summary(patched)
        run.analysis_config = _analysis_config(patched, self.agent_supervisor.agent_options())
        run.understood = _understood(patched)
        run.blocking_questions = questions
        run.events.append(_event("stage_completed", "analysis_run", "输入已更新", "已重新执行信息完整性检查。"))
        run.input_payload = patched
        self._save_run(run)
        return run

    def start(self, run_id: str) -> AnalysisRun:
        run = self.get(run_id)
        if run.status != "ready":
            raise ValueError("Run is not ready to start.")
        run.status = "queued"
        run.current_stage = "queued"
        run.inputs_frozen = True
        run.events.append(
            _event("stage_completed", "analysis_run", "任务已投递", "输入、规则快照和数据截止日期已冻结。")
        )
        self._save_run(run)
        job_id = dispatch_analysis_job(run.run_id, self)
        run = self.get(run.run_id)
        run.events.append(_event("tool_called", "worker_dispatcher", "后台任务已创建", f"job_id={job_id}"))
        self._save_run(run)
        return run

    def execute_queued(self, run_id: str) -> AnalysisRun:
        run = self.get(run_id)
        if run.status != "queued":
            raise ValueError("Run is not queued.")
        payload = _payload_from_run(run)
        return self._execute_pipeline(run, payload)

    def get(self, run_id: str) -> AnalysisRun:
        if self.use_database:
            with self.session_factory() as session:
                row = session.get(AnalysisRunRow, run_id)
                if row is None:
                    raise KeyError(run_id)
                return AnalysisRun.model_validate(row.payload)
        data = self._load_all()
        if run_id not in data:
            raise KeyError(run_id)
        return AnalysisRun.model_validate(data[run_id])

    def list_runs(self) -> list[AnalysisRun]:
        if self.use_database:
            with self.session_factory() as session:
                rows = session.scalars(select(AnalysisRunRow).order_by(AnalysisRunRow.created_at.desc())).all()
                return [AnalysisRun.model_validate(row.payload) for row in rows]
        return [AnalysisRun.model_validate(item) for item in self._load_all().values()]

    def events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        return self.get(run_id).events[after:]

    def evidence(self, run_id: str) -> list[RunEvidence]:
        return self.get(run_id).evidence

    def report(self, run_id: str) -> ReportDraft:
        report = self.get(run_id).report
        if report is None:
            raise KeyError("report")
        return report

    def trace(self, run_id: str) -> AnalysisTrace:
        run = self.get(run_id)
        return _build_trace_view(run)

    def _execute_pipeline(self, run: AnalysisRun, payload: AnalysisRunCreate) -> AnalysisRun:
        try:
            run = self._transition(run, "orchestra_planning", "stage_started", "Orchestra 编排分析计划", "基于用户需求和招股书摘要生成多阶段分析编队。")
            try:
                payload_for_orchestra = _payload_from_run(run)
                doc_summary = payload_for_orchestra.document_text[:2000]
                if not doc_summary and payload_for_orchestra.document_id:
                    try:
                        doc_summary = self.document_repository.get_text(payload_for_orchestra.document_id)[:2000]
                    except Exception:
                        doc_summary = ""
                plan = asyncio.run(
                    orchestrate(
                        user_requirement=payload_for_orchestra.ipo_project_hint
                        or payload_for_orchestra.company_name
                        or "对招股书进行风险分析",
                        company_name=run.company_name,
                        document_summary=doc_summary,
                        available_agents=self.agent_supervisor.agent_options(),
                    )
                )
                run.orchestra_plan = plan.model_dump()
                run.events.append(
                    _event("orchestra_plan_generated", "orchestrator", "Orchestra 编排完成", "已生成多阶段分析计划。")
                )
            except Exception as exc:
                run.events.append(
                    _event("stage_failed", "orchestrator", "Orchestra 编排失败（降级运行）", str(exc)[:200])
                )
            finally:
                run.events.append(_event("stage_completed", "orchestrator", "Orchestra 编排完成", "继续执行固定分析流水线。"))
                self._save_run(run)

            run = self._transition(run, "parsing", "stage_started", "解析招股书材料", "开始完整解析并切分原文块。")
            parsed_blocks = self._document_blocks(payload)
            run.document_blocks = _run_blocks_from_document(run.run_id, payload, parsed_blocks)
            text = _merge_text_and_data(
                "\n\n".join(block.text for block in run.document_blocks),
                payload.structured_data,
            )
            run.events.append(
                _event("stage_completed", "pdf_parser", "原文块解析完成", f"生成 {len(run.document_blocks)} 个可回溯原文块。")
            )

            run = self._transition(run, "indexing", "stage_started", "建立检索索引", "建立轻量全文和语义检索候选。")
            self.search_cache.clear()
            indexed_count = self.search_index.index_blocks(run_id=run.run_id, blocks=run.document_blocks)
            run.events.append(
                _event(
                    "stage_completed",
                    "hybrid_search",
                    "检索索引已就绪",
                    f"已索引 {indexed_count} 个 document_blocks，索引以原文块为事实源。",
                )
            )

            run = self._transition(run, "agent_running", "agent_started", "LangGraph Agent 团队开始分析", "按 active 规则快照执行结构化风险抽取。")
            max_findings = _effective_max_findings(payload)
            team_result = self.agent_supervisor.run(
                text=text,
                max_findings=max_findings,
                selected_agent_names=payload.selected_agent_names,
            )
            run.findings = self._findings_from_evaluations(team_result.evaluations, max_findings)
            run.events.extend(team_result.events)
            run.evidence = self._evidence_for_findings(run.run_id, run.findings, run.document_blocks)

            run = self._transition(run, "verifying", "stage_started", "Verifier 证据门禁", "校验证据存在性和报告准入状态。")
            run.verification_results = _verify_findings(run.findings, run.evidence)
            _apply_verification(run.findings, run.evidence, run.verification_results)
            run.events.append(
                _event("verification_completed", "verifier", "证据门禁完成", "仅 supported/weakly_supported/needs_human_review 进入报告。")
            )

            run = self._transition(run, "reporting", "stage_started", "生成报告草案", "Reporter 只读取已验证 finding 与证据。")
            run.report = _build_report(run)
            run.events.append(_event("report_section_generated", "reporter", "报告草案已生成", "review_status=draft。"))
            promoted = self._promote_supported_evidence(run)
            run.events.append(_event("stage_completed", "evidence_library", "证据晋升完成", f"晋升 {promoted} 条 supported 原文证据。"))

            run.status = "completed"
            run.current_stage = "completed"
            run.completed_at = _now()
            run.events.append(_event("stage_completed", "analysis_run", "分析完成", "端到端分析链路已完成。"))
            self.search_cache.collect_garbage()
            self._save_run(run)
            return run
        except Exception as exc:
            run.status = "failed"
            run.current_stage = "failed"
            run.events.append(_event("stage_failed", "analysis_run", "分析失败", str(exc)))
            self.search_cache.collect_garbage()
            self._save_run(run)
            raise

    def _document_text(self, payload: AnalysisRunCreate) -> str:
        if payload.document_text.strip():
            return payload.document_text
        if payload.document_id:
            return self.document_repository.get_text(payload.document_id)
        return ""

    def _document_blocks(self, payload: AnalysisRunCreate) -> list[ParsedDocumentBlock]:
        if payload.document_id:
            return self.document_repository.get_blocks(payload.document_id)
        document_id = f"doc_{hashlib.sha256(payload.document_text.encode('utf-8')).hexdigest()[:12]}"
        return [
            ParsedDocumentBlock(
                block_id=block.block_id,
                document_id=document_id,
                page_number=block.page_number,
                section_path=block.section_path,
                block_type=block.block_type,
                text=block.text,
                bbox=block.bbox,
                char_start=0,
                char_end=len(block.text),
                source_hash=block.source_hash,
                parser_version=block.parser_version,
            )
            for block in _parse_document_blocks("draft", payload, payload.document_text)
        ]

    def _transition(
        self,
        run: AnalysisRun,
        stage: str,
        event_type: str,
        title: str,
        summary: str,
    ) -> AnalysisRun:
        run.status = stage
        run.current_stage = stage
        run.events.append(_event(event_type, stage, title, summary))
        return run

    def _findings_from_evaluations(
        self,
        evaluations: list[RuleEvaluation],
        max_findings: int,
    ) -> list[RiskFinding]:
        triggered = [item for item in evaluations if item.evaluation_result == "triggered"]
        findings = []
        for idx, item in enumerate(triggered[:max_findings], start=1):
            findings.append(
                RiskFinding(
                    finding_id=f"finding_{idx:03d}",
                    agent_name=item.source_type if item.source_type.endswith("_agent") else _agent_for_evaluation(item),
                    rule_set_id=item.rule_set_id,
                    rule_version=item.rule_version,
                    rule_id=item.rule_id,
                    dimension_code=item.dimension_code,
                    rule_type=item.rule_type,
                    category=item.category,
                    title=item.title,
                    severity=_severity(item),
                    confidence=min(0.95, 0.55 + len(item.matched_terms) * 0.1),
                    trigger_condition=item.trigger_condition,
                    evaluation_result=item.evaluation_result,
                    reasoning_summary=item.reasoning_summary,
                    evidence_snippets=item.evidence_snippets[:2],
                    matched_terms=item.matched_terms,
                    requires_human_review=not item.evidence_snippets,
                )
            )
        return findings

    def _evidence_for_findings(
        self,
        run_id: str,
        findings: list[RiskFinding],
        blocks: list[DocumentBlock],
    ) -> list[RunEvidence]:
        evidence: list[RunEvidence] = []
        for finding in findings:
            candidates = self._hybrid_search(run_id, finding)
            for candidate in candidates[:2]:
                evidence_id = f"ev_{uuid.uuid4().hex[:10]}"
                evidence.append(
                    RunEvidence(
                        evidence_id=evidence_id,
                        finding_id=finding.finding_id,
                        document_block_id=candidate["block_id"],
                        page_number=candidate["page_number"],
                        section_path=candidate["section_path"],
                        text=candidate["text"][:300],
                        bbox=candidate["bbox"],
                        char_start=candidate["char_start"],
                        char_end=candidate["char_end"],
                        source_hash=candidate["source_hash"],
                        confidence=candidate["score"],
                        verifier_status="pending",
                    )
                )
                finding.evidence_ids.append(evidence_id)
        return evidence

    def _hybrid_search(self, run_id: str, finding: RiskFinding) -> list[dict[str, Any]]:
        query_terms = tuple(sorted(set(finding.matched_terms + [finding.category, finding.title])))
        cache_key = hashlib.sha256(f"{run_id}|{'|'.join(query_terms)}".encode("utf-8")).hexdigest()
        cached = self.search_cache.get(cache_key)
        if cached is not None:
            return cached
        candidates = [
            {
                "block_id": item.block_id,
                "page_number": item.page_number,
                "section_path": item.section_path,
                "text": item.text,
                "bbox": item.bbox,
                "char_start": item.char_start,
                "char_end": item.char_end,
                "source_hash": item.source_hash,
                "score": item.score,
            }
            for item in self.search_index.search(
                run_id=run_id,
                query=" ".join(query_terms),
                limit=5,
            )
        ]
        self.search_cache.set(cache_key, candidates)
        return candidates

    def _promote_supported_evidence(self, run: AnalysisRun) -> int:
        existing = self._load_evidence_library()
        by_key = {item["dedupe_key"]: item for item in existing}
        promoted = 0
        for item in run.evidence:
            if item.verifier_status != "supported":
                continue
            dedupe_key = hashlib.sha256(
                f"{item.document_block_id}|{item.text}".encode("utf-8")
            ).hexdigest()
            if dedupe_key in by_key:
                continue
            by_key[dedupe_key] = {
                "dedupe_key": dedupe_key,
                "source_run_id": run.run_id,
                "document_block_id": item.document_block_id,
                "page_number": item.page_number,
                "section_path": item.section_path,
                "text": item.text,
                "created_at": _now(),
            }
            promoted += 1
        self._save_evidence_library(list(by_key.values()))
        return promoted

    def _save_run(self, run: AnalysisRun) -> None:
        dumped = run.model_dump()
        dumped["input_payload"] = _payload_from_run(run).model_dump()
        dumped["frozen_rule_snapshot"] = run.frozen_rule_snapshot
        dumped["document_blocks"] = [item.model_dump() for item in run.document_blocks]
        dumped["evidence"] = [item.model_dump() for item in run.evidence]
        dumped["verification_results"] = [item.model_dump() for item in run.verification_results]
        dumped["report"] = run.report.model_dump() if run.report else None
        if self.use_database:
            with self.session_factory() as session:
                row = session.get(AnalysisRunRow, run.run_id)
                if row is None:
                    row = AnalysisRunRow(
                        run_id=run.run_id,
                        company_name=run.company_name,
                        status=run.status,
                        current_stage=run.current_stage,
                        created_at=run.created_at,
                        completed_at=run.completed_at,
                        data_cutoff_date=run.data_cutoff_date,
                        inputs_frozen=run.inputs_frozen,
                        payload=dumped,
                    )
                    session.add(row)
                else:
                    row.company_name = run.company_name
                    row.status = run.status
                    row.current_stage = run.current_stage
                    row.completed_at = run.completed_at
                    row.inputs_frozen = run.inputs_frozen
                    row.payload = dumped
                session.commit()
            return
        data = self._load_all()
        data[run.run_id] = dumped
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def _load_all(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}

    def _load_evidence_library(self) -> list[dict[str, Any]]:
        if self.use_database:
            with self.session_factory() as session:
                rows = session.scalars(select(EvidenceLibraryRow)).all()
                return [
                    {
                        "dedupe_key": row.dedupe_key,
                        "source_run_id": row.source_run_id,
                        "document_block_id": row.document_block_id,
                        "page_number": row.page_number,
                        "section_path": row.section_path,
                        "text": row.text,
                        "created_at": row.created_at,
                    }
                    for row in rows
                ]
        if not self.evidence_library_path.exists():
            return []
        return yaml.safe_load(self.evidence_library_path.read_text(encoding="utf-8")) or []

    def _save_evidence_library(self, items: list[dict[str, Any]]) -> None:
        if self.use_database:
            with self.session_factory() as session:
                existing = {row.dedupe_key for row in session.scalars(select(EvidenceLibraryRow)).all()}
                for item in items:
                    if item["dedupe_key"] in existing:
                        continue
                    session.add(
                        EvidenceLibraryRow(
                            dedupe_key=item["dedupe_key"],
                            source_run_id=item["source_run_id"],
                            document_block_id=item["document_block_id"],
                            page_number=item["page_number"],
                            section_path=item["section_path"],
                            text=item["text"],
                            created_at=item["created_at"],
                        )
                    )
                session.commit()
            return
        self.evidence_library_path.parent.mkdir(parents=True, exist_ok=True)
        self.evidence_library_path.write_text(
            yaml.safe_dump(items, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )


def _payload_from_run(run: AnalysisRun) -> AnalysisRunCreate:
    raw = run.input_payload
    if isinstance(raw, AnalysisRunCreate):
        return raw
    if isinstance(raw, dict):
        return AnalysisRunCreate.model_validate(raw)
    summary = run.input_summary
    return AnalysisRunCreate(
        company_name=run.company_name,
        document_text=summary.get("document_text_preview", ""),
        structured_data={key: "" for key in summary.get("structured_data_keys", [])},
    )


def _blocking_questions(payload: AnalysisRunCreate) -> list[str]:
    questions = []
    has_subject = any([payload.company_name and payload.company_name != "待确认公司", payload.ipo_project_hint])
    has_material = any([payload.document_text.strip(), payload.document_id, payload.material_url])
    if not has_subject:
        questions.append("请提供公司名称、申请编号或明确的 IPO 项目线索。")
    if not has_material:
        questions.append("请上传招股书 PDF、提供可下载 URL 或选择已有 document_id。")
    if not payload.confirm_defaults:
        questions.append("请确认是否使用默认风险规则、默认 Agent 队伍和默认分析深度。")
    return questions


def _validate_rule_set(payload: AnalysisRunCreate, snapshot: dict[str, Any]) -> None:
    if payload.risk_rule_set_id and payload.risk_rule_set_id != snapshot["rule_set_id"]:
        raise ValueError("Only the active risk rule set is available for analysis.")


def _analysis_config(payload: AnalysisRunCreate, agent_options: list[dict[str, Any]]) -> dict[str, Any]:
    selected = payload.selected_agent_names or [item["agent_name"] for item in agent_options]
    titles = {item["agent_name"]: item["title"] for item in agent_options}
    return {
        "risk_rule_set_id": payload.risk_rule_set_id or "active",
        "analysis_depth": payload.analysis_depth,
        "selected_agents": [
            {"agent_name": agent_name, "title": titles.get(agent_name, agent_name)}
            for agent_name in selected
        ],
        "max_findings": _effective_max_findings(payload),
    }


def _effective_max_findings(payload: AnalysisRunCreate) -> int:
    depth_caps = {"quick": 8, "standard": 20, "deep": 40}
    return max(1, min(payload.max_findings, depth_caps[payload.analysis_depth]))


def _understood(payload: AnalysisRunCreate) -> dict[str, Any]:
    return {
        "company_name": payload.company_name or "待确认",
        "material_hint": "已提供材料" if any([payload.document_text, payload.document_id, payload.material_url]) else "待补充",
        "focus": sorted(payload.structured_data.keys())[:5],
    }


def _input_summary(payload: AnalysisRunCreate) -> dict[str, Any]:
    return {
        "document_text_chars": len(payload.document_text),
        "document_text_preview": payload.document_text[:300],
        "structured_data_keys": sorted(payload.structured_data.keys()),
        "has_document_id": bool(payload.document_id),
        "has_material_url": bool(payload.material_url),
    }


def _compact_rule_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_set_id": snapshot["rule_set_id"],
        "version": snapshot["version"],
        "name": snapshot.get("name", ""),
        "totals": snapshot["totals"],
        "source_workbook": snapshot.get("source_workbook"),
    }


def _parse_document_blocks(run_id: str, payload: AnalysisRunCreate, text: str) -> list[DocumentBlock]:
    document_id = payload.document_id or f"doc_{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"
    chunks = _chunk_text(text)
    blocks = []
    section = "招股书正文"
    for idx, chunk in enumerate(chunks, start=1):
        maybe_section = _detect_section(chunk)
        if maybe_section:
            section = maybe_section
        source_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        blocks.append(
            DocumentBlock(
                block_id=f"block_{run_id}_{idx:04d}",
                document_id=document_id,
                page_number=idx,
                section_path=section,
                text=chunk,
                bbox=[0.0, 0.0, 595.0, 842.0],
                char_start=(idx - 1) * 900,
                char_end=(idx - 1) * 900 + len(chunk),
                source_hash=source_hash,
            )
        )
    return blocks


def _run_blocks_from_document(
    run_id: str,
    payload: AnalysisRunCreate,
    blocks: list[ParsedDocumentBlock],
) -> list[DocumentBlock]:
    if not blocks:
        return _parse_document_blocks(run_id, payload, payload.document_text)
    return [
        DocumentBlock(
            block_id=block.block_id,
            document_id=block.document_id,
            page_number=block.page_number,
            section_path=block.section_path,
            block_type=block.block_type,
            text=block.text,
            bbox=block.bbox,
            char_start=block.char_start,
            char_end=block.char_end,
            source_hash=block.source_hash,
            parser_version=block.parser_version,
        )
        for block in blocks
    ]


def _chunk_text(text: str, *, size: int = 900) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}|\f", text) if part.strip()]
    if not paragraphs:
        paragraphs = [text.strip()] if text.strip() else []
    chunks: list[str] = []
    for paragraph in paragraphs:
        for start in range(0, len(paragraph), size):
            chunk = paragraph[start : start + size].strip()
            if chunk:
                chunks.append(chunk)
    return chunks or ["未提供可解析文本。"]


def _detect_section(text: str) -> str | None:
    for marker in ["风险因素", "关联交易", "财务资料", "业务", "历史及公司架构"]:
        if marker in text[:80]:
            return marker
    return None


def _score_block(text: str, terms: tuple[str, ...]) -> int:
    lower = text.lower()
    return sum(1 for term in terms if term and term.lower() in lower)


def _verify_findings(findings: list[RiskFinding], evidence: list[RunEvidence]) -> list[VerificationResult]:
    by_finding: dict[str, list[RunEvidence]] = {}
    for item in evidence:
        by_finding.setdefault(item.finding_id, []).append(item)
    results = []
    for finding in findings:
        linked = by_finding.get(finding.finding_id, [])
        strong = [item for item in linked if item.confidence >= 0.55]
        if finding.severity == "high" and strong:
            status = "supported"
            reason = "高风险发现绑定了至少一条强证据。"
        elif linked:
            status = "weakly_supported"
            reason = "发现有原文证据，但强度不足或需人工复核。"
        else:
            status = "needs_human_review"
            reason = "未找到可回溯原文证据。"
        results.append(
            VerificationResult(
                finding_id=finding.finding_id,
                status=status,
                checked_evidence_ids=[item.evidence_id for item in linked],
                reason=reason,
                created_at=_now(),
            )
        )
    return results


def _apply_verification(
    findings: list[RiskFinding],
    evidence: list[RunEvidence],
    results: list[VerificationResult],
) -> None:
    status_by_finding = {item.finding_id: item.status for item in results}
    for finding in findings:
        status = status_by_finding.get(finding.finding_id, "needs_human_review")
        finding.requires_human_review = status in {"weakly_supported", "needs_human_review"}
    for item in evidence:
        item.verifier_status = status_by_finding.get(item.finding_id, "needs_human_review")


def _build_report(run: AnalysisRun) -> ReportDraft:
    allowed = {"supported", "weakly_supported", "needs_human_review"}
    status_by_finding = {item.finding_id: item.status for item in run.verification_results}
    report_findings = [
        finding for finding in run.findings if status_by_finding.get(finding.finding_id) in allowed
    ]
    lines = [
        f"# {run.company_name} IPO 风险分析草案",
        "",
        f"- 数据截止日期：{run.data_cutoff_date}",
        f"- 规则版本：{run.rule_snapshot['rule_set_id']} / {run.rule_snapshot['version']}",
        "- 复核状态：draft",
        "",
        "## 关键风险发现",
    ]
    for finding in report_findings:
        status = status_by_finding.get(finding.finding_id, "needs_human_review")
        lines.extend(
            [
                "",
                f"### {finding.title}",
                f"- 规则：{finding.rule_id}",
                f"- 严重程度：{finding.severity}",
                f"- 证据门禁：{status}",
                f"- 摘要：{finding.reasoning_summary}",
            ]
        )
    if not report_findings:
        lines.append("未发现满足证据门禁的关键风险结论。")
    markdown = "\n".join(lines)
    html = markdown.replace("\n", "<br>")
    return ReportDraft(
        report_id=f"report_{uuid.uuid4().hex[:10]}",
        markdown=markdown,
        html=html,
        created_at=_now(),
    )


def _build_trace_view(run: AnalysisRun) -> AnalysisTrace:
    stages = [
        TraceStage(key="orchestra_planning", label="Orchestra 编排", owner="orchestrator", status=_stage_status(run, "orchestra_planning")),
        TraceStage(key="ready", label="输入确认", owner="analysis_run", status=_stage_status(run, "ready")),
        TraceStage(key="queued", label="任务投递", owner="worker_dispatcher", status=_stage_status(run, "queued")),
        TraceStage(key="parsing", label="PDF 解析", owner="pdf_parser", status=_stage_status(run, "parsing")),
        TraceStage(key="indexing", label="检索索引", owner="hybrid_search", status=_stage_status(run, "indexing")),
        TraceStage(key="agent_running", label="LangGraph Agent 团队", owner="orchestrator", status=_stage_status(run, "agent_running")),
        TraceStage(key="verifying", label="证据门禁", owner="verifier", status=_stage_status(run, "verifying")),
        TraceStage(key="reporting", label="报告生成", owner="reporter", status=_stage_status(run, "reporting")),
        TraceStage(key="completed", label="签发交付", owner="analysis_run", status=_stage_status(run, "completed")),
    ]
    flow = [_trace_event(run.created_at, event) for event in run.events]
    evidence = [
        TraceEvidenceItem(
            id=item.evidence_id,
            source="官方",
            title=f"{item.section_path} P.{item.page_number}",
            conf=round(item.confidence, 4),
            time="近实时",
        )
        for item in run.evidence[:20]
    ]
    return AnalysisTrace(stages=stages, seed=flow[:8], flow=flow, evidenceFeed=evidence)


def _stage_status(run: AnalysisRun, key: str) -> str:
    order = ["orchestra_planning", "ready", "queued", "parsing", "indexing", "agent_running", "verifying", "reporting", "completed"]
    if run.current_stage == key and run.status not in {"completed", "failed"}:
        return "running"
    try:
        current = order.index(run.current_stage)
        target = order.index(key)
    except ValueError:
        return "queued"
    return "done" if current >= target or run.status == "completed" else "queued"


def _trace_event(run_created_at: str, event: dict[str, Any]) -> TraceEvent:
    return TraceEvent(
        who=str(event.get("actor_name") or "analysis_run"),
        kind=_trace_kind(str(event.get("event_type") or "")),
        text=_trace_text(event),
        t=_relative_time(run_created_at, str(event.get("created_at") or run_created_at)),
    )


def _trace_kind(event_type: str) -> str:
    if event_type in {"agent_started", "stage_started", "tool_called"}:
        return "execute"
    if event_type in {"agent_completed", "stage_completed", "report_section_generated"}:
        return "discover"
    if event_type == "verification_completed":
        return "verdict"
    if event_type == "stage_failed":
        return "challenge"
    return "assign"


def _trace_text(event: dict[str, Any]) -> str:
    title = str(event.get("title") or "")
    summary = str(event.get("summary") or "")
    return f"{title}：{summary}" if summary else title


def _relative_time(start: str, current: str) -> str:
    try:
        start_dt = datetime.fromisoformat(start)
        current_dt = datetime.fromisoformat(current)
    except ValueError:
        return "00:00"
    seconds = max(0, int((current_dt - start_dt).total_seconds()))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _merge_text_and_data(document_text: str, structured_data: dict[str, Any]) -> str:
    if not structured_data:
        return document_text
    data_lines = ["结构化数据："]
    for key, value in sorted(structured_data.items()):
        data_lines.append(f"{key}: {value}")
    return f"{document_text}\n\n" + "\n".join(data_lines)


def _severity(item: RuleEvaluation) -> str:
    high_terms = {
        "对赌",
        "赎回",
        "持续经营",
        "诉讼",
        "担保",
        "现金流",
        "资产负债率",
        "valuation adjustment",
        "redemption",
        "going concern",
        "litigation",
        "guarantee",
        "cash flow",
    }
    if high_terms.intersection(item.matched_terms):
        return "high"
    if len(item.matched_terms) >= 2:
        return "medium"
    return "low"


def _agent_for_evaluation(item: RuleEvaluation) -> str:
    if item.rule_type == "quantitative_financial":
        return "financial_agent"
    if item.category in {"关联交易", "对赌/赎回条款", "政策监管", "诉讼与纠纷", "担保与质押", "税务风险"}:
        return "legal_agent"
    if item.rule_type == "textual_nlp":
        return "business_agent"
    if item.category in {"行业竞争"}:
        return "market_agent"
    return "business_agent"


def _event(event_type: str, actor_name: str, title: str, summary: str) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "actor_name": actor_name,
        "title": title,
        "summary": summary,
        "created_at": _now(),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_default_analysis_repository() -> AnalysisRunRepository:
    return AnalysisRunRepository()
