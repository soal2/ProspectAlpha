"""Local MVP analysis-run service for source-grounded IPO risk checks."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import threading
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from riskagent.agent_experts import AgentSpec
from riskagent.agents.exceptions import DebateLLMError
from riskagent.agents.orchestrator import (
    IntegratedRiskReport,
    StrategyDecision,
    synthesize_report,
    validate_report_references,
)
from riskagent.agents.supervisor import AgentTeamSupervisor
from riskagent.common.cache import TTLCache
from riskagent.database import Base, SessionLocal, init_db, make_engine
from riskagent.documents import (
    DocumentBlock as ParsedDocumentBlock,
    DocumentRepository,
    get_default_document_repository,
)
from riskagent.risk_rules import RiskRuleRepository, get_default_repository
from riskagent.skills.risk_extract import RuleEvaluation
from riskagent.skills.risk_skill_registry import (
    skill_for_rule,
    skills_for_agents,
)
from riskagent.seed_data import AGENT_SPECS
from riskagent.seed_data import AGENT_EXPERTS
from riskagent.models import AnalysisRunRow, EvidenceLibraryRow, ReportLibraryRow
from riskagent.providers.llm import get_default_llm_client
from riskagent.retrieval import ChunkSearchResult, PostgresChunkIndexer, prospectus_hybrid_search
from riskagent.retrieval.query_planner import (
    build_rule_search_queries,
    rerank_rule_candidates,
)
from riskagent.workers.analysis import dispatch_analysis_job


REPORT_SYSTEM_PROMPT = """你是 ProspectAlpha 风险分析报告的章节撰写者。下面是"{company_name}"招股书在某个维度的 finding 与证据。请基于这些结构化数据，用专业、严谨、可追溯的中文写一段 200-500 字的风险分析。要求：1. 直接输出 markdown 段落正文，不要写章节标题；2. 每个判断必须引用证据；3. 不要重复罗列 finding 字段名；4. 如果证据不足，明确指出；5. 只输出正文段落。"""
BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANALYSIS_RUNS_PATH = BACKEND_ROOT / "data" / "processed" / "analysis_runs.yaml"
DEFAULT_EVIDENCE_LIBRARY_PATH = BACKEND_ROOT / "data" / "processed" / "evidence_library.yaml"
PARSER_VERSION = "local-text-parser-v1"
_FILE_LOCKS: dict[str, threading.RLock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _invoke_chunk_retriever(retriever: Callable[..., Any], **kwargs: Any) -> Any:
    """Pass optional ranking controls only when a custom retriever supports them."""

    signature = inspect.signature(retriever)
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if not accepts_kwargs:
        kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
    return retriever(**kwargs)

PIPELINE_STAGES: list[tuple[str, str, str]] = [
    ("task_understanding", "任务理解", "任务理解专家"),
    ("pdf_parsing", "PDF 解析", "PDF 解析器"),
    ("expert_orchestration", "专家编排", "沈北辰"),
    ("evidence_extraction", "证据抽取", "证据检索专家"),
    ("risk_identification", "风险识别", "专业 Agent 团队"),
    ("expert_debate", "专家辩论", "周慎言 · 红队仲裁"),
    ("cross_validation", "交叉验证", "证据审计专家"),
    ("report_generation", "报告生成", "报告撰写专家"),
    ("quality_review", "质检审裁", "质检审裁专家"),
    ("completed", "交付完成", "analysis_run"),
]


class AnalysisRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str = "待确认公司"
    document_text: str = ""
    document_id: str | None = None
    material_url: str | None = None
    ipo_project_hint: str | None = None
    confirm_defaults: bool = True
    risk_rule_set_id: str | None = None
    analysis_depth: Literal["quick", "standard", "deep"] = "standard"
    structured_data: dict[str, Any] = Field(default_factory=dict)
    max_findings: int = 30


class AnalysisRunInputPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str | None = None
    document_text: str | None = None
    document_id: str | None = None
    material_url: str | None = None
    ipo_project_hint: str | None = None
    confirm_defaults: bool | None = None
    risk_rule_set_id: str | None = None
    analysis_depth: Literal["quick", "standard", "deep"] | None = None
    structured_data: dict[str, Any] | None = None
    max_findings: int | None = None


class ExpertSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_names: list[str] = Field(min_length=1)

    @classmethod
    def validate_names(cls, names: list[str]) -> list[str]:
        selected = [name.strip() for name in names]
        if not selected or any(not name for name in selected):
            raise ValueError("At least one expert must be selected.")
        if len(selected) != len(set(selected)):
            raise ValueError("Selected expert names must be unique.")
        return selected


class DocumentBlock(BaseModel):
    block_id: str
    document_id: str
    page_number: int
    section_path: str
    block_type: str = "text"
    text: str
    bbox: list[float]
    bbox_coordinate_space: Literal["pdf_points", "normalized_1000"] = "pdf_points"
    char_start: int = 0
    char_end: int = 0
    source_hash: str
    parser_version: str = PARSER_VERSION


class RunEvidence(BaseModel):
    evidence_id: str
    finding_id: str
    document_block_id: str
    document_id: str = ""
    page_number: int
    section_path: str
    text: str
    bbox: list[float] = Field(default_factory=list)
    bbox_coordinate_space: Literal["pdf_points", "normalized_1000"] | None = None
    char_start: int = 0
    char_end: int = 0
    source_hash: str = ""
    retrieval_score: float = 0.0
    relevance_score: float = 0.0
    verifier_status: str

    @model_validator(mode="after")
    def _backfill_document_id(self) -> "RunEvidence":
        # Legacy runs persisted evidence without document_id; recover it from the
        # block_id format `block_{doc_id}_{page:04d}_{idx:03d}`.
        if not self.document_id and self.document_block_id:
            match = re.match(r"^block_(.+)_\d{4}_\d{3}$", self.document_block_id)
            if match:
                self.document_id = match.group(1)
        if self.bbox_coordinate_space is None:
            self.bbox_coordinate_space = (
                "normalized_1000"
                if "_chunk_" in self.document_block_id
                else "pdf_points"
            )
        return self


class VerificationResult(BaseModel):
    finding_id: str
    status: str
    checked_evidence_ids: list[str]
    reason: str
    created_at: str


class RiskFinding(BaseModel):
    finding_id: str
    agent_name: str = "rule_agent"
    skill_id: str | None = None
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
    verification_status: str = "pending"


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
    page: int | None = None
    snippet: str | None = None
    document_id: str | None = None
    bbox: list[float] = Field(default_factory=list)


class AnalysisTrace(BaseModel):
    stages: list[TraceStage]
    seed: list[TraceEvent]
    flow: list[TraceEvent]
    evidenceFeed: list[TraceEvidenceItem]


class PdfProgress(BaseModel):
    status: str = "pending"
    parsed_pages: int = 0
    total_pages: int = 0
    parsed_blocks: int = 0
    error: str | None = None


class IndexProgress(BaseModel):
    status: str = "pending"
    indexed_blocks: int = 0
    total_blocks: int = 0


class AgentCollaborationProgress(BaseModel):
    agent_name: str
    module_id: str
    module: str
    status: str
    latest_action: str
    latest_summary: str


class AnalysisProgress(BaseModel):
    run_id: str
    phase: str
    updated_at: str
    status: str = "ready"
    current_stage: str = "ready"
    error_summary: str | None = None
    pdf: PdfProgress
    index: IndexProgress
    agents: list[AgentCollaborationProgress] = Field(default_factory=list)


class AnalysisRun(BaseModel):
    run_id: str
    company_name: str
    status: str
    current_stage: str
    failed_stage: str | None = None
    error_summary: str | None = None
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
    strategy_plan: dict[str, Any] = Field(default_factory=dict)
    team_state: list[dict[str, Any]] = Field(default_factory=list)
    interrupt_reason: str | None = None
    replacement_history: list[dict[str, Any]] = Field(default_factory=list)
    module_results: list[dict[str, Any]] = Field(default_factory=list)
    debate_history: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    frozen_rule_snapshot: dict[str, Any] = Field(default_factory=dict, exclude=True)
    document_blocks: list[DocumentBlock] = Field(default_factory=list, exclude=True)
    findings: list[RiskFinding] = Field(default_factory=list)
    evidence: list[RunEvidence] = Field(default_factory=list, exclude=True)
    verification_results: list[VerificationResult] = Field(default_factory=list, exclude=True)
    report: ReportDraft | None = Field(default=None, exclude=True)
    events: list[dict[str, Any]] = Field(default_factory=list)
    input_payload: AnalysisRunCreate | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _discard_legacy_preselected_agents(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        legacy_payload = value.get("input_payload")
        if not isinstance(legacy_payload, dict) or "selected_agent_names" not in legacy_payload:
            return value
        normalized = dict(value)
        normalized_payload = dict(legacy_payload)
        normalized_payload.pop("selected_agent_names")
        normalized["input_payload"] = normalized_payload
        return normalized


class AnalysisRunRepository:
    def __init__(
        self,
        path: Path | str | None = None,
        rule_repository: RiskRuleRepository | None = None,
        evidence_library_path: Path | str | None = None,
        document_repository: DocumentRepository | None = None,
        session_factory=SessionLocal,
        use_database: bool | None = None,
        chunk_retriever: Callable[..., list[ChunkSearchResult]] | None = None,
        agent_supervisor: AgentTeamSupervisor | None = None,
        report_provider: Callable[..., Any] | None = None,
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
        self.use_database = use_database if use_database is not None else os.getenv("USE_DATABASE", "1") != "0"
        if self.use_database:
            bind = getattr(session_factory, "kw", {}).get("bind")
            if bind is None:
                init_db()
            else:
                Base.metadata.create_all(bind=bind)
            self.session_factory = session_factory
        else:
            retrieval_path = self.path.with_suffix(".retrieval.db")
            retrieval_path.parent.mkdir(parents=True, exist_ok=True)
            retrieval_engine = make_engine(f"sqlite:///{retrieval_path}")
            Base.metadata.create_all(bind=retrieval_engine)
            self.session_factory = sessionmaker(
                bind=retrieval_engine,
                autoflush=False,
                autocommit=False,
                future=True,
            )
        self.search_cache: TTLCache[list[dict[str, Any]]] = TTLCache(ttl_seconds=30, max_items=256)
        self.chunk_retriever = chunk_retriever or prospectus_hybrid_search
        self.agent_supervisor = agent_supervisor or AgentTeamSupervisor(
            repository=self.rule_repository,
            agent_specs=None if self.use_database else _seed_agent_specs(),
        )
        self.report_provider = report_provider or synthesize_report
        self._published_event_counts: dict[str, int] = {}

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
                "agent_selection": "dynamic_strategy",
            },
            events=[
                _event(
                    "stage_started",
                    "analysis_run",
                    "创建分析任务",
                    "已完成信息完整性检查，等待用户确认规则与分析深度；专家编队将在启动后动态生成。",
                    stage_key="task_understanding",
                    stage_status="ready" if status == "ready" else "waiting_user",
                    action_label="任务创建",
                ),
            ],
            progress={
                "phase": status,
                "updated_at": now,
                "pdf": {"status": "pending"},
                "index": {"status": "pending"},
            },
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

    def start(self, run_id: str, *, dispatch_mode: str | None = None) -> AnalysisRun:
        run = self.get(run_id)
        if run.status != "ready":
            raise ValueError("Run is not ready to start.")
        run.status = "queued"
        run.current_stage = "queued"
        run.inputs_frozen = True
        run.events.append(
            _event(
                "stage_completed",
                "analysis_run",
                "任务已投递",
                "输入、规则快照和数据截止日期已冻结，后续 Agent 只读取本次快照。",
                stage_key="task_understanding",
                stage_status="completed",
                action_label="输入冻结",
                payload={"rule_set_id": run.rule_snapshot["rule_set_id"], "version": run.rule_snapshot["version"]},
            )
        )
        self._save_run(run)
        run.events.append(
            _event(
                "tool_started",
                "worker_dispatcher",
                "后台任务已创建",
                "分析任务已提交给后台执行器。",
                stage_key="task_understanding",
                action_label="worker dispatch",
                metrics={"tool_calls": 1},
            )
        )
        self._save_run(run)
        job_id = dispatch_analysis_job(run.run_id, self, mode=dispatch_mode)
        if dispatch_mode == "thread":
            return run
        run = self.get(run.run_id)
        run.events.append(
            _event(
                "tool_completed",
                "worker_dispatcher",
                "后台任务已入队",
                f"job_id={job_id}",
                stage_key="task_understanding",
                action_label="worker dispatch",
            )
        )
        self._save_run(run)
        return run

    def resume(self, run_id: str, *, dispatch_mode: str | None = None) -> AnalysisRun:
        run = self.get(run_id)
        if run.status in {"completed", "failed"}:
            return run
        if run.status == "ready":
            return self.start(run_id, dispatch_mode=dispatch_mode)
        if not run.inputs_frozen:
            raise ValueError("Run inputs are not frozen; start the run first.")
        run.status = "queued"
        run.current_stage = "queued"
        run.events.append(
            _event(
                "tool_started",
                "worker_dispatcher",
                "恢复后台任务",
                "检测到分析任务停留在非终态阶段，已重新投递后台执行。",
                stage_key="task_understanding",
                action_label="resume dispatch",
                metrics={"tool_calls": 1},
            )
        )
        self._save_run(run)
        job_id = dispatch_analysis_job(run.run_id, self, mode=dispatch_mode)
        if dispatch_mode == "thread":
            return run
        run = self.get(run.run_id)
        run.events.append(
            _event(
                "tool_completed",
                "worker_dispatcher",
                "后台恢复任务已入队",
                f"job_id={job_id}",
                stage_key="task_understanding",
                action_label="resume dispatch",
            )
        )
        self._save_run(run)
        return run

    def execute_queued(self, run_id: str) -> AnalysisRun:
        run = self.get(run_id)
        if run.status != "queued":
            raise ValueError("Run is not queued.")
        payload = _payload_from_run(run)
        return self._execute_pipeline(run, payload)

    def select_experts(
        self,
        run_id: str,
        agent_names: list[str],
        *,
        dispatch_mode: str | None = None,
    ) -> AnalysisRun:
        run = self.get(run_id)
        if run.status != "awaiting_expert_selection":
            raise ValueError("Run is not waiting for expert selection.")
        selected = ExpertSelectionRequest.validate_names(agent_names)
        available = {
            str(item.get("agent_name", ""))
            for item in self.agent_supervisor.agent_options()
        }
        unknown = sorted(set(selected) - available)
        if unknown:
            raise ValueError(f"Unknown experts: {', '.join(unknown)}")
        if run.strategy_plan:
            plan = StrategyDecision.model_validate(run.strategy_plan)
            completed_modules = {
                str(item.get("module_id"))
                for item in run.module_results
                if item.get("module_id")
            }
            pending_modules = [
                item.module_id
                for item in plan.assignments
                if item.module_id not in completed_modules
            ]
            if pending_modules and len(selected) != len(pending_modules):
                raise ValueError(
                    "Select exactly one expert for each pending module: "
                    f"pending_modules={len(pending_modules)}"
                )
        run.analysis_config["manual_agent_names"] = selected
        run.interrupt_reason = None
        run.status = "queued"
        run.current_stage = "expert_orchestration"
        run.events.append(
            _event(
                "manual_expert_selection_submitted",
                "user",
                "人工专家选择已提交",
                f"选择 {len(selected)} 位专家，系统将依据 Agent Card 自动派题并恢复 LangGraph。",
                stage_key="expert_orchestration",
                action_label="恢复协作",
                payload={"agent_names": selected},
            )
        )
        self._save_run(run)
        dispatch_analysis_job(run.run_id, self, mode=dispatch_mode)
        return self.get(run.run_id)

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
        run = self.get(run_id)
        if run.evidence:
            return run.evidence
        return self._recover_rejected_evidence(run)

    def _recover_rejected_evidence(self, run: AnalysisRun) -> list[RunEvidence]:
        """Recover legacy debate-rejected evidence from audit events and PG chunks."""
        if not self.use_database:
            return []
        event_payloads = [
            event.get("payload") or {}
            for event in run.events
            if event.get("event_type") == "evidence_found"
            and isinstance(event.get("payload"), dict)
        ]
        document_ids = {
            str(payload.get("document_id") or "") for payload in event_payloads
        } - {""}
        if not document_ids:
            return []
        indexer = PostgresChunkIndexer(ensure=False)
        try:
            chunks_by_document = {
                document_id: indexer.list_document_chunks(document_id)
                for document_id in document_ids
            }
        finally:
            indexer.close()

        recovered: list[RunEvidence] = []
        seen_evidence_ids: set[str] = set()
        for payload in event_payloads:
            document_id = str(payload.get("document_id") or "")
            evidence_id = str(payload.get("evidence_id") or "")
            if evidence_id in seen_evidence_ids:
                continue
            try:
                page_number = int(payload.get("page_number") or 0)
                bbox = [float(value) for value in payload.get("bbox") or []]
                confidence = float(payload.get("confidence") or 0)
            except (TypeError, ValueError):
                continue
            if page_number < 1 or len(bbox) != 4:
                continue
            block_id = str(payload.get("document_block_id") or "")
            candidates = chunks_by_document.get(document_id, [])
            chunk = next((item for item in candidates if item.chunk_id == block_id), None)
            if chunk is None:
                target_bbox = tuple(round(value, 4) for value in bbox)
                chunk = next(
                    (
                        item
                        for item in candidates
                        if item.page_idx == page_number
                        and any(
                            isinstance(region, dict)
                            and int(region.get("page_idx", -1)) == page_number
                            and tuple(
                                round(float(value), 4)
                                for value in region.get("bbox", [])
                            )
                            == target_bbox
                            for region in item.merged_bboxes
                        )
                    ),
                    None,
                )
            if chunk is None:
                continue
            metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
            recovered_evidence_id = evidence_id or f"ev_{chunk.chunk_id}"
            if recovered_evidence_id in seen_evidence_ids:
                continue
            coordinate_space = str(metadata.get("bbox_coordinate_space") or "pdf_points")
            if coordinate_space not in {"pdf_points", "normalized_1000"}:
                coordinate_space = "pdf_points"
            recovered.append(
                RunEvidence(
                    evidence_id=recovered_evidence_id,
                    finding_id=str(payload.get("finding_id") or "rejected_finding"),
                    document_block_id=chunk.chunk_id,
                    document_id=document_id,
                    page_number=page_number,
                    section_path=str(metadata.get("section_path") or chunk.chunk_type),
                    text=chunk.text[:300],
                    bbox=bbox,
                    bbox_coordinate_space=coordinate_space,
                    char_start=int(metadata.get("char_start") or 0),
                    char_end=int(metadata.get("char_end") or 0),
                    source_hash=str(
                        metadata.get("source_hash")
                        or hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
                    ),
                    retrieval_score=confidence,
                    relevance_score=0.0,
                    verifier_status="rejected_by_debate",
                )
            )
            seen_evidence_ids.add(recovered_evidence_id)
        return recovered

    def report(self, run_id: str) -> ReportDraft:
        report = self.get(run_id).report
        if report is None:
            raise KeyError("report")
        return report

    def trace(self, run_id: str) -> AnalysisTrace:
        run = self.get(run_id)
        return _build_trace_view(run)

    def progress(self, run_id: str) -> AnalysisProgress:
        return _build_analysis_progress(self.get(run_id))

    def append_event(self, run_id: str, event: dict[str, Any]) -> AnalysisRun:
        """Append one durable event without replacing worker-written events."""

        run = self.get(run_id)
        item = dict(event)
        item.setdefault("event_id", f"evt_{uuid.uuid4().hex}")
        item.setdefault("created_at", _now())
        run.events = _merge_events(run.events, [item])
        self._save_run(run)
        return run

    def _execute_pipeline(self, run: AnalysisRun, payload: AnalysisRunCreate) -> AnalysisRun:
        try:
            run = self._transition(
                run,
                "task_understanding",
                "stage_started",
                "任务理解专家已收到请求",
                "根据招股书 PDF 分析该公司的港股 IPO 上市风险。",
                actor_name="task_understanding_agent",
                action_label="理解任务",
            )
            run.understood = _understood(payload)
            run.events.append(
                _event(
                    "stage_completed",
                    "task_understanding_agent",
                    "系统识别到本次任务属于港股 IPO 招股书风险分析",
                    "默认关注财务风险、业务风险、合规风险、估值风险与上市后破发风险。",
                    stage_key="task_understanding",
                    stage_status="completed",
                    action_label="任务确认",
                    payload=run.understood,
                )
            )
            self._save_run(run)

            run = self._transition(
                run,
                "pdf_parsing",
                "stage_started",
                "PDF 解析器开始解析招股书材料",
                "解析文本、页码、章节、bbox，并为后续 Agent 建立可回溯材料底座。",
                actor_name="pdf_parser",
                action_label="解析中",
            )
            total_pages = 0
            if payload.document_id:
                try:
                    total_pages = self.document_repository.get(payload.document_id).page_count
                except KeyError:
                    if not payload.document_text.strip():
                        raise
            run.progress = {
                **run.progress,
                "phase": "pdf_parsing",
                "updated_at": _now(),
                "pdf": {
                    "status": "parsing",
                    "parsed_pages": 0,
                    "total_pages": total_pages,
                    "parsed_blocks": 0,
                },
                "index": {"status": "pending", "indexed_blocks": 0, "total_blocks": 0},
            }
            self._save_run(run)

            def _on_parse_progress(done: int, total: int, blocks: int) -> None:
                run.progress = {
                    **run.progress,
                    "phase": "pdf_parsing",
                    "updated_at": _now(),
                    "pdf": {
                        "status": "parsing",
                        "parsed_pages": done,
                        "total_pages": total,
                        "parsed_blocks": blocks,
                    },
                }
                self._save_run(run)

            parsed_blocks = self._document_blocks(payload, on_progress=_on_parse_progress)
            run.document_blocks = _run_blocks_from_document(run.run_id, payload, parsed_blocks)
            text = _merge_text_and_data(
                "\n\n".join(block.text for block in run.document_blocks),
                payload.structured_data,
            )
            block_stats = _document_block_stats(run.document_blocks)
            run.progress = {
                **run.progress,
                "updated_at": _now(),
                "pdf": {
                    "status": "completed",
                    "parsed_pages": total_pages or max((item.page_number for item in run.document_blocks), default=0),
                    "total_pages": total_pages or max((item.page_number for item in run.document_blocks), default=0),
                    "parsed_blocks": len(run.document_blocks),
                },
                "index": {
                    "status": "indexing",
                    "indexed_blocks": 0,
                    "total_blocks": len(run.document_blocks),
                },
            }
            run.events.append(
                _event(
                    "tool_completed",
                    "pdf_parser",
                    "招股书原文块解析完成",
                    f"已提取 {len(run.document_blocks)} 个关键段落、{block_stats['table_blocks']} 张表格、{block_stats['risk_tagged_blocks']} 条风险相关原文块。",
                    stage_key="pdf_parsing",
                    action_label="解析完成",
                    metrics=block_stats,
                )
            )
            run.events.append(
                _event(
                    "tool_started",
                    "hybrid_search",
                    "校验 PostgreSQL 材料索引",
                    "复用上传阶段按 document_id 写入的 PostgreSQL 全文与向量索引。",
                    stage_key="pdf_parsing",
                    action_label="建立索引",
                    metrics={"tool_calls": 1},
                )
            )
            self.search_cache.clear()
            progress_state = {"last_emitted": 0}

            def _on_index_progress(done: int, total: int) -> None:
                if total <= 0:
                    return
                run.progress = {
                    **run.progress,
                    "updated_at": _now(),
                    "index": {
                        "status": "indexing",
                        "indexed_blocks": done,
                        "total_blocks": total,
                    },
                }
                # Persist every batch; emit SSE audit events only every ~10%.
                threshold = progress_state["last_emitted"] + max(1, total // 10)
                if done < threshold and done < total:
                    self._save_run(run)
                    return
                progress_state["last_emitted"] = done
                run.events.append(
                    _event(
                        "tool_progress",
                        "hybrid_search",
                        "索引校验中",
                        f"已确认 {done}/{total} 个原文块的文档映射。",
                        stage_key="pdf_parsing",
                        action_label="向量化",
                        metrics={"embedded_blocks": done, "total_blocks": total},
                    )
                )
                self._save_run(run)

            indexed_count = len(run.document_blocks)
            _on_index_progress(indexed_count, indexed_count)
            run.progress = {
                **run.progress,
                "updated_at": _now(),
                "pdf": {
                    "status": "completed",
                    "parsed_pages": total_pages or max((item.page_number for item in run.document_blocks), default=0),
                    "total_pages": total_pages or max((item.page_number for item in run.document_blocks), default=0),
                    "parsed_blocks": len(run.document_blocks),
                },
                "index": {
                    "status": "completed",
                    "indexed_blocks": indexed_count,
                    "total_blocks": len(run.document_blocks),
                },
            }
            run.events.append(
                _event(
                    "stage_completed",
                    "hybrid_search",
                    "PDF 解析与 PostgreSQL 材料索引已就绪",
                    f"已确认 {indexed_count} 个 document_blocks，检索按 document_id 隔离。",
                    stage_key="pdf_parsing",
                    stage_status="completed",
                    action_label="材料库就绪",
                    metrics={"indexed_blocks": indexed_count, **block_stats},
                )
            )
            self._save_run(run)

            run = self._transition(
                run,
                "expert_orchestration",
                "stage_started",
                "沈北辰开始选择专家编队",
                "根据任务和风险维度选择 Domain Skills，并从冻结规则快照分配规则。",
                actor_name="orchestrator",
                action_label="专家编排",
            )
            payload_for_orchestra = _payload_from_run(run)
            doc_summary = payload_for_orchestra.document_text[:4000]
            if not doc_summary and payload_for_orchestra.document_id:
                try:
                    doc_summary = self.document_repository.get_text(
                        payload_for_orchestra.document_id
                    )[:4000]
                except Exception:
                    doc_summary = ""
            manual_agent_names = list(
                run.analysis_config.pop("manual_agent_names", []) or []
            )
            team_event_lock = threading.Lock()

            def _persist_team_event(event: dict[str, Any]) -> None:
                # Expert threads may emit concurrently.  Keep the in-flight run
                # object and durable event log aligned so the final save cannot
                # overwrite a message the browser has already seen.
                with team_event_lock:
                    event.setdefault("event_id", f"evt_{uuid.uuid4().hex}")
                    event.setdefault("created_at", _now())
                    run.events = _merge_events(run.events, [event])
                    self._save_run(run)

            if manual_agent_names:
                team_result = self.agent_supervisor.resume_with_selection(
                    run_id=run.run_id,
                    agent_names=manual_agent_names,
                    event_sink=_persist_team_event,
                )
            else:
                team_result = self.agent_supervisor.run(
                    run_id=run.run_id,
                    company_name=run.company_name,
                    user_requirement=payload_for_orchestra.ipo_project_hint
                    or payload_for_orchestra.company_name
                    or "对招股书进行风险分析",
                    document_summary=doc_summary,
                    text=text,
                    document_id=payload_for_orchestra.document_id or "",
                    max_findings=_effective_max_findings(payload),
                    rule_snapshot=run.frozen_rule_snapshot,
                    event_sink=_persist_team_event,
                )
            run.events = _merge_events(run.events, team_result.events)
            run.strategy_plan = team_result.strategy_plan
            run.orchestra_plan = team_result.strategy_plan
            run.team_state = team_result.team_state
            run.replacement_history = team_result.replacement_history
            run.module_results = team_result.module_results
            if team_result.paused:
                run.status = "awaiting_expert_selection"
                run.current_stage = "expert_orchestration"
                run.interrupt_reason = team_result.interrupt_reason or "战略层等待人工选择专家。"
                run.events.append(
                    _event(
                        "analysis_paused",
                        "orchestrator",
                        "动态协作已暂停",
                        run.interrupt_reason,
                        stage_key="expert_orchestration",
                        stage_status="waiting_user",
                        action_label="等待人工接管",
                        payload={"available_agents": self.agent_supervisor.agent_options()},
                    )
                )
                self._save_run(run)
                return run
            plan = StrategyDecision.model_validate(run.strategy_plan)
            available_by_name = {
                str(item["agent_name"]): item
                for item in self.agent_supervisor.agent_options()
            }
            active_agent_names = list(
                dict.fromkeys(
                    str(item.get("agent_name"))
                    for item in run.module_results
                    if item.get("agent_name")
                )
            ) or plan.selected_agents
            selected_skills = skills_for_agents(active_agent_names)
            skills_by_agent = {skill.agent_name: skill for skill in selected_skills}
            run.analysis_config["strategy_selected_agents"] = plan.selected_agents
            run.analysis_config["selected_agents"] = [
                {
                    "agent_name": name,
                    "title": str(available_by_name.get(name, {}).get("title") or name),
                }
                for name in active_agent_names
            ]
            run.interrupt_reason = None
            for agent_name in active_agent_names:
                skill = skills_by_agent.get(agent_name)
                option = available_by_name.get(agent_name, {})
                role = str(
                    (skill.role_zh if skill else None)
                    or option.get("role")
                    or option.get("title")
                    or agent_name
                )
                skill_id = skill.skill_id if skill else option.get("skill_id")
                summary = (
                    f"启用 {skill_id}，将按冻结规则快照检索和输出结构化证据。"
                    if skill_id
                    else "按实时 Agent Card 能力与战略任务执行本次分析。"
                )
                run.events.append(
                    _event(
                        "agent_selected",
                        agent_name,
                        f"{role}加入本次风险调研",
                        summary,
                        stage_key="expert_orchestration",
                        skill_id=str(skill_id) if skill_id else None,
                        agent_role=role,
                        action_label="专家入场",
                        payload={"skill_id": skill_id, "agent_name": agent_name},
                    )
                )
            run.events.append(
                _event(
                    "stage_completed",
                    "orchestrator",
                    "专家编排完成",
                    f"已选择 {len(active_agent_names)} 位专家参与本次风险调研。",
                    stage_key="expert_orchestration",
                    stage_status="completed",
                    action_label="编排完成",
                    metrics={"selected_agents": len(active_agent_names)},
                )
            )
            self._save_run(run)

            run = self._transition(
                run,
                "evidence_extraction",
                "stage_started",
                "证据抽取阶段开始",
                "专业 Agent 根据各自 Skill 与冻结规则快照检索招股书材料。",
                actor_name="orchestrator",
                action_label="证据抽取",
            )
            self._save_run(run)
            max_findings = _effective_max_findings(payload)
            candidate_findings = self._findings_from_evaluations(team_result.evaluations, max_findings)
            run.evidence = self._evidence_for_findings(run.run_id, candidate_findings, run.document_blocks)
            before_dedupe = len(run.evidence)
            run.evidence = _dedupe_evidence(run.evidence)
            findings_by_agent_rule = {
                (item.agent_name, item.rule_id): item for item in candidate_findings
            }
            for module_result in run.module_results:
                module_agent = str(module_result.get("agent_name") or "")
                linked_findings = [
                    findings_by_agent_rule[(module_agent, rule_id)]
                    for rule_id in module_result.get("rule_ids", [])
                    if (module_agent, rule_id) in findings_by_agent_rule
                ]
                module_result["finding_ids"] = [item.finding_id for item in linked_findings]
                module_result["evidence_ids"] = list(
                    dict.fromkeys(
                        evidence_id
                        for finding in linked_findings
                        for evidence_id in finding.evidence_ids
                    )
                )
            for item in run.evidence[:50]:
                finding = next((candidate for candidate in candidate_findings if candidate.finding_id == item.finding_id), None)
                run.events.append(
                    _event(
                        "evidence_found",
                        finding.agent_name if finding else "evidence_extractor",
                        f"发现证据：{finding.title if finding else item.section_path}",
                        f"{item.section_path} 第 {item.page_number} 页：{item.text[:80]}",
                        stage_key="evidence_extraction",
                        skill_id=finding.skill_id if finding else None,
                        action_label="发现",
                        payload={
                            "evidence_id": item.evidence_id,
                            "finding_id": item.finding_id,
                            "document_block_id": item.document_block_id,
                            "document_id": item.document_id,
                            "page_number": item.page_number,
                            "bbox": item.bbox,
                            "bbox_coordinate_space": item.bbox_coordinate_space,
                            "retrieval_score": item.retrieval_score,
                            "relevance_score": item.relevance_score,
                        },
                    )
                )
            run.events.append(
                _event(
                    "evidence_deduped",
                    "evidence_audit_skill",
                    "证据审计专家完成初步去重",
                    f"去重 {before_dedupe - len(run.evidence)} 条重复证据，保留 {len(run.evidence)} 条有效证据。",
                    stage_key="evidence_extraction",
                    skill_id="evidence_audit_skill",
                    agent_role="证据审计专家",
                    action_label="去重",
                    metrics={"before": before_dedupe, "after": len(run.evidence)},
                )
            )
            run.events.append(
                _event(
                    "stage_completed",
                    "evidence_extractor",
                    "证据抽取完成",
                    f"已形成 {len(candidate_findings)} 条候选风险判断并绑定 {len(run.evidence)} 条 PDF 原文证据。",
                    stage_key="evidence_extraction",
                    stage_status="completed",
                    action_label="证据库更新",
                )
            )
            self._save_run(run)

            run = self._transition(
                run,
                "risk_identification",
                "stage_started",
                "风险识别阶段开始",
                "专家基于规则命中与证据材料形成初步风险判断。",
                actor_name="orchestrator",
                action_label="风险识别",
            )
            run.findings = candidate_findings
            for finding in run.findings:
                run.events.append(
                    _event(
                        "risk_finding_created",
                        finding.agent_name,
                        f"形成初步风险判断：{finding.title}",
                        finding.reasoning_summary,
                        stage_key="risk_identification",
                        skill_id=finding.skill_id,
                        action_label="形成判断",
                        payload={
                            "finding_id": finding.finding_id,
                            "rule_id": finding.rule_id,
                            "severity": finding.severity,
                            "confidence": finding.confidence,
                            "evidence_ids": finding.evidence_ids,
                        },
                    )
                )
            run.events.append(
                _event(
                    "stage_completed",
                    "orchestrator",
                    "风险识别完成",
                    f"已形成 {len(run.findings)} 条结构化风险判断，等待交叉验证。",
                    stage_key="risk_identification",
                    stage_status="completed",
                    action_label="判断汇总",
                )
            )
            self._save_run(run)

            run = self._run_debate_stage(run)
            if run.status == "awaiting_human_review":
                return run

            run = self._transition(
                run,
                "cross_validation",
                "stage_started",
                "交叉验证阶段开始",
                "证据审计专家检查每条风险判断是否有 PDF 原文证据支撑。",
                actor_name="verifier",
                action_label="交叉验证",
            )
            run.verification_results = _verify_findings(run.findings, run.evidence)
            _apply_verification(run.findings, run.evidence, run.verification_results)
            for result in run.verification_results:
                finding = next((item for item in run.findings if item.finding_id == result.finding_id), None)
                run.events.append(
                    _event(
                        "risk_finding_verified",
                        "verifier",
                        f"验证风险判断：{finding.title if finding else result.finding_id}",
                        result.reason,
                        stage_key="cross_validation",
                        skill_id="evidence_audit_skill",
                        agent_role="证据审计专家",
                        action_label="校验",
                        payload=result.model_dump(),
                    )
                )
            run.events.append(
                _event(
                    "stage_completed",
                    "verifier",
                    "交叉验证完成",
                    "仅 supported/weakly_supported 进入正式结论；needs_human_review 保留为待人工补证事项。",
                    stage_key="cross_validation",
                    stage_status="completed",
                    skill_id="evidence_audit_skill",
                    action_label="验证完成",
                )
            )
            self._save_run(run)

            run = self._transition(
                run,
                "report_generation",
                "stage_started",
                "战略层 Agent 开始整合风险报告",
                "战略层只读取全部专家模块结果、已验证 finding 与证据引用。",
                actor_name="orchestrator",
                action_label="报告生成",
            )
            allowed_statuses = {"supported", "weakly_supported"}
            admitted_finding_ids = {
                item.finding_id
                for item in run.verification_results
                if item.status in allowed_statuses
            }
            admitted_findings = [
                item for item in run.findings if item.finding_id in admitted_finding_ids
            ]
            admitted_evidence = [
                item
                for item in run.evidence
                if item.finding_id in admitted_finding_ids
                and item.verifier_status in allowed_statuses
            ]
            admitted_evidence_ids = {item.evidence_id for item in admitted_evidence}
            sanitized_module_results = []
            for module in run.module_results:
                module_finding_ids = [
                    fid
                    for fid in module.get("finding_ids", [])
                    if str(fid) in admitted_finding_ids
                ]
                module_evidence_ids = [
                    eid
                    for eid in module.get("evidence_ids", [])
                    if str(eid) in admitted_evidence_ids
                ]
                sanitized_module_results.append(
                    {
                        **module,
                        "summary": (
                            f"本模块有 {len(module_finding_ids)} 条风险判断通过证据门禁，"
                            "仅可依据随附 finding 与 evidence 撰写。"
                            if module_finding_ids
                            else "本模块未形成通过证据门禁的风险结论；不得据此宣称不存在风险。"
                        ),
                        "finding_ids": module_finding_ids,
                        "evidence_ids": module_evidence_ids,
                    }
                )
            integrated: IntegratedRiskReport | None = None
            synthesis_error: Exception | None = None
            if not admitted_findings:
                integrated = _empty_verified_report(run, sanitized_module_results)
            for attempt in range(2 if admitted_findings else 0):
                try:
                    integrated = IntegratedRiskReport.model_validate(
                        _resolve_provider_result(
                            self.report_provider(
                                company_name=run.company_name,
                                data_cutoff_date=run.data_cutoff_date,
                                module_results=sanitized_module_results,
                                verified_findings=[
                                    item.model_dump() for item in admitted_findings
                                ],
                                evidence_index=[
                                    {
                                        "evidence_id": item.evidence_id,
                                        "finding_id": item.finding_id,
                                        "page_number": item.page_number,
                                        "section_path": item.section_path,
                                        "retrieval_score": item.retrieval_score,
                                        "relevance_score": item.relevance_score,
                                        "text": item.text,
                                    }
                                    for item in admitted_evidence
                                ],
                                validation_feedback=(
                                    str(synthesis_error) if synthesis_error else None
                                ),
                            )
                        )
                    )
                    validate_report_references(
                        integrated,
                        module_ids={
                            str(item["module_id"])
                            for item in sanitized_module_results
                            if item.get("module_id")
                        },
                        finding_ids=admitted_finding_ids,
                        finding_ids_by_module={
                            str(module["module_id"]): {
                                str(finding_id)
                                for finding_id in module.get("finding_ids", [])
                                if str(finding_id) in admitted_finding_ids
                            }
                            for module in sanitized_module_results
                            if module.get("module_id")
                        },
                        evidence_by_finding={
                            finding_id: {
                                item.evidence_id
                                for item in admitted_evidence
                                if item.finding_id == finding_id
                            }
                            for finding_id in admitted_finding_ids
                        },
                    )
                    synthesis_error = None
                    break
                except Exception as exc:
                    synthesis_error = exc
            if integrated is None or synthesis_error is not None:
                raise ValueError(
                    f"Strategic report synthesis failed validation: {synthesis_error}"
                )
            run.report = _build_integrated_report(run, integrated)
            run.events.append(
                _event(
                    "report_section_generated",
                    "orchestrator",
                    "战略层完整报告已生成",
                    f"已融合 {len(run.module_results)} 个专家模块并完成跨模块风险整合。",
                    stage_key="report_generation",
                    action_label="章节生成",
                    metrics={"report_chars": len(run.report.markdown)},
                )
            )
            run.events.append(
                _event(
                    "stage_completed",
                    "orchestrator",
                    "报告生成完成",
                    "等待质检审裁。",
                    stage_key="report_generation",
                    stage_status="completed",
                    action_label="报告完成",
                )
            )
            self._save_run(run)

            run = self._transition(
                run,
                "quality_review",
                "stage_started",
                "质检审裁专家开始检查最终报告",
                "检查核心判断证据、页码引用、风险等级一致性与无证据结论。",
                actor_name="quality_reviewer",
                action_label="质检",
            )
            qa_result = _quality_review(run)
            if run.report:
                run.report.review_status = qa_result["review_status"]
            if qa_result["issues"]:
                for issue in qa_result["issues"]:
                    run.events.append(
                        _event(
                            "qa_issue_found",
                            "quality_reviewer",
                            "发现风险判断证据不足",
                            issue,
                            stage_key="quality_review",
                            skill_id="quality_review_skill",
                            agent_role="质检审裁专家",
                            action_label="退回补充",
                        )
                    )
            else:
                has_verified_findings = bool(admitted_finding_ids)
                run.events.append(
                    _event(
                        "qa_passed" if has_verified_findings else "qa_no_verified_findings",
                        "quality_reviewer",
                        "质检审裁通过" if has_verified_findings else "质检完成：无已验证结论",
                        (
                            "所有核心结论均附有可回溯证据来源。"
                            if has_verified_findings
                            else "未形成通过证据门禁的正式结论，报告已明确标记为未评级。"
                        ),
                        stage_key="quality_review",
                        skill_id="quality_review_skill",
                        agent_role="质检审裁专家",
                        action_label="通过",
                    )
                )
            run.events.append(
                _event(
                    "stage_completed",
                    "quality_reviewer",
                    "质检审裁完成",
                    f"review_status={qa_result['review_status']}。",
                    stage_key="quality_review",
                    stage_status="completed",
                    action_label="审裁完成",
                    metrics={"issues": len(qa_result["issues"])},
                )
            )
            promoted = self._promote_supported_evidence(run)
            run.events.append(
                _event(
                    "tool_completed",
                    "evidence_library",
                    "证据晋升完成",
                    f"晋升 {promoted} 条 supported 原文证据。",
                    stage_key="quality_review",
                    action_label="证据沉淀",
                )
            )

            archived = self._archive_report(run)
            if archived:
                run.events.append(
                    _event(
                        "tool_completed",
                        "report_library",
                        "报告沉淀完成",
                        f"报告已入知识库，共沉淀 {archived} 个分节。",
                        stage_key="quality_review",
                        action_label="报告沉淀",
                    )
                )

            run.status = "completed"
            run.current_stage = "completed"
            run.completed_at = _now()
            run.events.append(
                _event(
                    "stage_completed",
                    "analysis_run",
                    "风险调研任务已完成",
                    "最终报告已生成，核心结论均可查看完整证据链。",
                    stage_key="completed",
                    stage_status="completed",
                    action_label="交付完成",
                    metrics={
                        "evidence_count": len(run.evidence),
                        "finding_count": len(run.findings),
                        "agent_count": len(selected_skills),
                    },
                )
            )
            self.search_cache.collect_garbage()
            self._save_run(run)
            return run
        except Exception as exc:
            failed_stage = run.current_stage
            if failed_stage == "pdf_parsing":
                run.progress = {
                    **run.progress,
                    "updated_at": _now(),
                    "pdf": {**run.progress.get("pdf", {}), "status": "failed", "error": str(exc)},
                }
            run.status = "failed"
            run.current_stage = "failed"
            run.failed_stage = failed_stage
            run.error_summary = str(exc)
            run.events.append(
                _event(
                    "stage_failed",
                    "analysis_run",
                    "分析失败",
                    str(exc),
                    stage_key=failed_stage,
                    stage_status="failed",
                )
            )
            self.search_cache.collect_garbage()
            self._save_run(run)
            raise

    def _document_text(self, payload: AnalysisRunCreate) -> str:
        if payload.document_text.strip():
            return payload.document_text
        if payload.document_id:
            return self.document_repository.get_text(payload.document_id)
        return ""

    def _document_blocks(
        self,
        payload: AnalysisRunCreate,
        *,
        on_progress: Callable[[int, int, int], None] | None = None,
    ) -> list[ParsedDocumentBlock]:
        if payload.document_id:
            try:
                self.document_repository.prepare_for_retrieval(
                    payload.document_id,
                    on_progress=on_progress,
                )
                return self.document_repository.get_blocks(payload.document_id)
            except KeyError:
                if not payload.document_text.strip():
                    raise
        document_id = payload.document_id or (
            f"doc_{hashlib.sha256(payload.document_text.encode('utf-8')).hexdigest()[:12]}"
        )
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
        *,
        actor_name: str | None = None,
        action_label: str | None = None,
    ) -> AnalysisRun:
        run.status = stage
        run.current_stage = stage
        run.events.append(
            _event(
                event_type,
                actor_name or stage,
                title,
                summary,
                stage_key=stage,
                stage_status="running",
                action_label=action_label,
            )
        )
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
                    agent_name=item.source_type.strip() or "unattributed_expert",
                    skill_id=item.skill_id or _skill_id_for_evaluation(item),
                    rule_set_id=item.rule_set_id,
                    rule_version=item.rule_version,
                    rule_id=item.rule_id,
                    dimension_code=item.dimension_code,
                    rule_type=item.rule_type,
                    category=item.category,
                    title=item.title,
                    severity=_severity(item),
                    confidence=max(0.0, min(1.0, item.confidence)),
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
        payload = _payload_from_run(self.get(run_id))
        for finding in findings:
            candidates = self._hybrid_search(run_id, finding)
            if not candidates and not payload.document_id:
                candidates = _block_candidates_for_finding(finding, blocks)
            for candidate in candidates[:3]:
                evidence_id = f"ev_{uuid.uuid4().hex[:10]}"
                evidence.append(
                    RunEvidence(
                        evidence_id=evidence_id,
                        finding_id=finding.finding_id,
                        document_block_id=candidate["block_id"],
                        document_id=str(candidate.get("document_id") or ""),
                        page_number=candidate["page_number"],
                        section_path=candidate["section_path"],
                        text=_candidate_excerpt(candidate, limit=300),
                        bbox=candidate["bbox"],
                        bbox_coordinate_space=candidate["bbox_coordinate_space"],
                        char_start=candidate["char_start"],
                        char_end=candidate["char_end"],
                        source_hash=candidate["source_hash"],
                        retrieval_score=float(candidate.get("retrieval_score") or 0.0),
                        relevance_score=float(candidate.get("relevance_score") or 0.0),
                        verifier_status="pending",
                    )
                )
                finding.evidence_ids.append(evidence_id)
        return evidence

    def _hybrid_search(self, run_id: str, finding: RiskFinding) -> list[dict[str, Any]]:
        rule = self.rule_repository.get_rule(finding.rule_id)
        payload = _payload_from_run(self.get(run_id))
        queries = build_rule_search_queries(
            rule,
            user_requirement=payload.ipo_project_hint or "",
        )
        cache_key = hashlib.sha256(
            f"{run_id}|{finding.rule_id}|{'|'.join(queries)}".encode("utf-8")
        ).hexdigest()
        cached = self.search_cache.get(cache_key)
        if cached is not None:
            return cached
        document_id = payload.document_id
        if not document_id:
            return []
        try:
            self.document_repository.get(document_id)
        except KeyError:
            if payload.document_text.strip():
                return []
            raise
        recalled: list[dict[str, Any]] = []
        for query_index, query in enumerate(queries):
            recalled.extend(
                _chunk_evidence_candidate(item)
                for item in _invoke_chunk_retriever(
                    self.chunk_retriever,
                    query=query,
                    document_id=document_id,
                    limit=8,
                    vector_weight=0.35 if query_index < 3 else 0.65,
                )
            )
        candidates = rerank_rule_candidates(recalled, rule=rule, limit=5)
        self.search_cache.set(cache_key, candidates)
        return candidates

    def _retrieve_supplemental_evidence(
        self,
        run: AnalysisRun,
        *,
        eid: str | None = None,
        agent_name: str | None = None,
        finding_id: str | None = None,
        query: str | None = None,
    ) -> list[RunEvidence]:
        target_finding = next(
            (finding for finding in run.findings if finding.finding_id == finding_id),
            None,
        )
        if target_finding is None and agent_name:
            target_finding = next(
                (finding for finding in run.findings if finding.agent_name == agent_name),
                None,
            )
        if target_finding is None:
            target_finding = run.findings[0] if run.findings else None
        if target_finding is None:
            return []

        search_query = " ".join(
            part
            for part in [
                query,
                target_finding.title,
                target_finding.category,
                target_finding.reasoning_summary,
                " ".join(target_finding.matched_terms),
            ]
            if part
        ).strip()
        if not search_query:
            search_query = eid or target_finding.title

        payload = _payload_from_run(run)
        document_id = payload.document_id
        if not document_id:
            return []
        try:
            self.document_repository.get(document_id)
        except KeyError:
            if payload.document_text.strip():
                return []
            raise
        rule = self.rule_repository.get_rule(target_finding.rule_id)
        queries = list(
            dict.fromkeys(
                [search_query, *build_rule_search_queries(rule, user_requirement=query or "")]
            )
        )[:6]
        recalled: list[dict[str, Any]] = []
        for query_index, rewritten_query in enumerate(queries):
            recalled.extend(
                _chunk_evidence_candidate(item)
                for item in _invoke_chunk_retriever(
                    self.chunk_retriever,
                    query=rewritten_query,
                    document_id=document_id,
                    limit=8,
                    vector_weight=0.35 if query_index < 3 else 0.65,
                )
            )
        candidates = rerank_rule_candidates(recalled, rule=rule, limit=3)
        evidence: list[RunEvidence] = []
        for index, candidate in enumerate(candidates):
            evidence_id = str(eid) if eid and index == 0 else f"ev_{uuid.uuid4().hex[:10]}"
            evidence.append(
                RunEvidence(
                    evidence_id=evidence_id,
                    finding_id=target_finding.finding_id,
                    document_block_id=candidate["block_id"],
                    document_id=candidate["document_id"],
                    page_number=candidate["page_number"],
                    section_path=candidate["section_path"],
                    text=_candidate_excerpt(candidate, limit=300),
                    bbox=candidate["bbox"],
                    bbox_coordinate_space=candidate["bbox_coordinate_space"],
                    char_start=candidate["char_start"],
                    char_end=candidate["char_end"],
                    source_hash=candidate["source_hash"],
                    retrieval_score=candidate["retrieval_score"],
                    relevance_score=candidate["relevance_score"],
                    verifier_status="pending",
                )
            )
        return evidence

    def _run_supplement_loop(self, run: AnalysisRun, required: list[Any]) -> AnalysisRun:
        existing_evidence_ids = {item.evidence_id for item in run.evidence}
        for item in required:
            if isinstance(item, dict):
                finding_id = str(item.get("finding_id") or "") or None
                agent_name = str(item.get("agent_name") or "") or None
                reason = str(item.get("reason") or item.get("query") or item.get("supplement") or "")
            else:
                finding_id = None
                agent_name = None
                reason = str(item)
            supplemental = [
                evidence
                for evidence in self._retrieve_supplemental_evidence(
                    run,
                    finding_id=finding_id,
                    agent_name=agent_name,
                    query=reason,
                )
                if evidence.evidence_id not in existing_evidence_ids
            ]
            for evidence in supplemental:
                existing_evidence_ids.add(evidence.evidence_id)
                finding = next(
                    (candidate for candidate in run.findings if candidate.finding_id == evidence.finding_id),
                    None,
                )
                if finding and evidence.evidence_id not in finding.evidence_ids:
                    finding.evidence_ids.append(evidence.evidence_id)
            run.evidence.extend(supplemental)
            run.events.append(
                _event(
                    "supplement_evidence_collected",
                    agent_name or "evidence_extractor",
                    "补充证据检索完成",
                    f"根据红队要求补充 {len(supplemental)} 条证据。",
                    stage_key="expert_debate",
                    action_label="补证",
                    payload={
                        "finding_id": finding_id,
                        "agent_name": agent_name,
                        "reason": reason,
                        "evidence_ids": [evidence.evidence_id for evidence in supplemental],
                    },
                )
            )
        return run

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

    def _archive_report(self, run: AnalysisRun) -> int:
        """Persist the final report into the report library, idempotently per run."""

        if run.report is None or not run.report.markdown.strip():
            return 0
        sections = _report_sections(run.report.markdown)
        try:
            payload = _payload_from_run(run)
            document_id = payload.document_id or ""
        except Exception:
            document_id = ""
        try:
            with self.session_factory() as session:
                existing = session.get(ReportLibraryRow, run.report.report_id) or session.scalar(
                    select(ReportLibraryRow).where(ReportLibraryRow.source_run_id == run.run_id)
                )
                if existing is None:
                    session.add(
                        ReportLibraryRow(
                            report_id=run.report.report_id,
                            source_run_id=run.run_id,
                            company_name=run.company_name,
                            document_id=document_id,
                            rule_set_id=str(run.rule_snapshot.get("rule_set_id") or ""),
                            overall_risk_level=_overall_risk_level(run.findings),
                            finding_count=len(run.findings),
                            markdown=run.report.markdown,
                            sections=sections,
                            created_at=_now(),
                        )
                    )
                else:
                    existing.markdown = run.report.markdown
                    existing.sections = sections
                    existing.overall_risk_level = _overall_risk_level(run.findings)
                    existing.finding_count = len(run.findings)
                session.commit()
        except Exception as exc:
            run.events.append(
                _event(
                    "tool_failed",
                    "report_library",
                    "报告入库失败",
                    f"{type(exc).__name__}: {exc}",
                    stage_key="quality_review",
                    action_label="报告沉淀",
                )
            )
            return 0
        return len([section for section in sections if section["text"].strip()])

    def _run_debate_stage(self, run: AnalysisRun) -> AnalysisRun:
        """Run the 2-round expert debate phase between risk_identification and cross_validation."""

        from riskagent.agents.debate import run_debate_phase_sync
        from riskagent.runtime_events import LiveLLMCall

        run = self._transition(
            run,
            "expert_debate",
            "stage_started",
            "专家辩论阶段开始",
            "每位专家对同侪 finding 进行质证，第二轮综合反馈修订，最后由 L3 红队仲裁。",
            actor_name="orchestrator",
            action_label="专家辩论",
        )
        self._save_run(run)

        finding_payloads = [
            {
                "finding_id": item.finding_id,
                "agent_name": item.agent_name,
                "rule_id": item.rule_id,
                "title": item.title,
                "severity": item.severity,
                "confidence": item.confidence,
                "reasoning_summary": item.reasoning_summary,
                "evidence_ids": list(item.evidence_ids),
                "evidence": [
                    {
                        "evidence_id": evidence.evidence_id,
                        "page_number": evidence.page_number,
                        "section_path": evidence.section_path,
                        "text": evidence.text,
                        "relevance_score": evidence.relevance_score,
                    }
                    for evidence in run.evidence
                    if evidence.finding_id == item.finding_id
                ],
            }
            for item in run.findings
        ]
        module_results = list(run.module_results)
        run.events.append(
            _event(
                "debate_round_started",
                "orchestrator",
                "辩论第 1 轮：质证",
                f"{len({item.agent_name for item in run.findings})} 位专家开始互相质证 {len(finding_payloads)} 条 finding。",
                stage_key="expert_debate",
                action_label="质证启动",
                payload={"round": 1, "finding_count": len(finding_payloads)},
            )
        )
        self._publish_event(
            run,
            _event(
                "llm_call_started",
                "orchestrator",
                "专家辩论 LLM 调用开始",
                f"准备对 {len(finding_payloads)} 条 finding 发起辩论阶段 LLM 调用。",
                stage_key="expert_debate",
                action_label="LLM 调用开始",
                payload={"phase": "debate", "finding_count": len(finding_payloads)},
            ),
        )
        try:
            def _debate_live_call(agent_name: str) -> LiveLLMCall:
                return LiveLLMCall(
                    run_id=run.run_id,
                    agent_name=agent_name,
                    provider="openai-compatible",
                    model=os.getenv("OPENAI_MODEL", "unknown"),
                    persist=lambda event: self._publish_event(run, event),
                )

            debate_result = run_debate_phase_sync(
                company_name=run.company_name,
                findings=finding_payloads,
                module_results=module_results,
                live_call_factory=_debate_live_call,
            )
        except DebateLLMError as exc:
            self._publish_event(
                run,
                _event(
                    "llm_call_failed",
                    "orchestrator",
                    "专家辩论 LLM 调用失败",
                    f"辩论 {exc.phase} 阶段失败：{exc.failed}/{exc.total} — {exc.detail}",
                    stage_key="expert_debate",
                    stage_status="failed",
                    action_label="LLM 调用失败",
                    payload={
                        "error": str(exc),
                        "phase": exc.phase,
                        "failed": exc.failed,
                        "total": exc.total,
                        "detail": exc.detail,
                    },
                ),
            )
            run.events.append(
                _event(
                    "debate_round_failed",
                    "orchestrator",
                    "专家辩论失败",
                    f"辩论 {exc.phase} 阶段失败：{exc.failed}/{exc.total} — {exc.detail}",
                    stage_key="expert_debate",
                    stage_status="failed",
                    action_label="辩论失败",
                    payload={
                        "error": str(exc),
                        "phase": exc.phase,
                        "failed": exc.failed,
                        "total": exc.total,
                        "detail": exc.detail,
                    },
                )
            )
            run.events.append(
                _event(
                    "stage_completed",
                    "orchestrator",
                    "专家辩论阶段结束（降级）",
                    "辩论阶段未能完成，已直接进入交叉验证。",
                    stage_key="expert_debate",
                    stage_status="completed",
                    action_label="辩论跳过",
                )
            )
            self._save_run(run)
            return run
        except Exception as exc:  # pragma: no cover - defensive guard for unexpected failures
            self._publish_event(
                run,
                _event(
                    "llm_call_failed",
                    "orchestrator",
                    "专家辩论 LLM 调用失败",
                    f"辩论阶段因异常中止：{exc}",
                    stage_key="expert_debate",
                    stage_status="failed",
                    action_label="LLM 调用失败",
                    payload={"phase": "debate", "error": str(exc)[:200]},
                ),
            )
            run.events.append(
                _event(
                    "debate_round_failed",
                    "orchestrator",
                    "专家辩论失败",
                    f"辩论阶段因异常中止：{exc}",
                    stage_key="expert_debate",
                    stage_status="failed",
                    action_label="辩论失败",
                    payload={"error": str(exc)},
                )
            )
            run.events.append(
                _event(
                    "stage_completed",
                    "orchestrator",
                    "专家辩论阶段结束（降级）",
                    "辩论阶段未能完成，已直接进入交叉验证。",
                    stage_key="expert_debate",
                    stage_status="completed",
                    action_label="辩论跳过",
                )
            )
            self._save_run(run)
            return run

        self._publish_event(
            run,
            _event(
                "llm_call_completed",
                "orchestrator",
                "专家辩论 LLM 调用完成",
                f"收到 {len(debate_result.critiques)} 条质证意见和 {len(debate_result.revisions)} 份修订记录。",
                stage_key="expert_debate",
                stage_status="completed",
                action_label="LLM 调用完成",
                payload={
                    "phase": "debate",
                    "critique_count": len(debate_result.critiques),
                    "revision_count": len(debate_result.revisions),
                    "decision": debate_result.ruling.decision,
                },
            ),
        )
        for critique in debate_result.critiques:
            run.events.append(
                _event(
                    "peer_critique_sent",
                    str(critique.get("critic_agent", "")),
                    f"质证 {critique.get('target_agent', '')} 的 finding",
                    str(critique.get("critique_summary", "")),
                    stage_key="expert_debate",
                    action_label="质证",
                    payload={
                        "round": 1,
                        "critic_agent": critique.get("critic_agent"),
                        "target_agent": critique.get("target_agent"),
                        "finding_ids": critique.get("finding_ids", []),
                        "severity": critique.get("severity", "info"),
                        "suggested_revisions": critique.get("suggested_revisions", []),
                    },
                )
            )
        run.events.append(
            _event(
                "debate_round_completed",
                "orchestrator",
                "辩论第 1 轮：质证完成",
                f"共发出 {len(debate_result.critiques)} 条质证意见。",
                stage_key="expert_debate",
                action_label="质证汇总",
                payload={"round": 1, "critique_count": len(debate_result.critiques)},
            )
        )
        run.events.append(
            _event(
                "debate_round_started",
                "orchestrator",
                "辩论第 2 轮：修订",
                "每位专家综合质证意见修订自身 finding。",
                stage_key="expert_debate",
                action_label="修订启动",
                payload={"round": 2},
            )
        )
        for revision in debate_result.revisions:
            run.events.append(
                _event(
                    "expert_revised",
                    str(revision.get("agent_name", "")),
                    f"{revision.get('agent_name', '')} 完成证据修订",
                    str(revision.get("revision_reason", "")),
                    stage_key="expert_debate",
                    action_label="修订",
                    payload={
                        "round": 2,
                        "agent_name": revision.get("agent_name"),
                        "original_finding_ids": revision.get("original_finding_ids", []),
                        "added_evidence_ids": revision.get("added_evidence_ids", []),
                        "removed_finding_ids": revision.get("removed_finding_ids", []),
                    },
                )
            )
        run.events.append(
            _event(
                "debate_round_completed",
                "orchestrator",
                "辩论第 2 轮：修订完成",
                f"共收到 {len(debate_result.revisions)} 份修订记录。",
                stage_key="expert_debate",
                action_label="修订汇总",
                payload={"round": 2, "revision_count": len(debate_result.revisions)},
            )
        )

        ruling = debate_result.ruling
        ruling_event_type = "debate_arbiter_ruling"
        if ruling.decision == "request_supplement":
            ruling_event_type = "evidence_supplement_requested"
        run.events.append(
            _event(
                ruling_event_type,
                "zhou_shenyan_redteam_agent",
                f"红队仲裁：{ruling.decision}",
                ruling.reasoning,
                stage_key="expert_debate",
                skill_id="redteam_skill",
                agent_role="质量总检 · 红队",
                action_label="仲裁裁决",
                payload={
                    "decision": ruling.decision,
                    "required_supplements": ruling.required_supplements,
                    "flagged_finding_ids": ruling.flagged_finding_ids,
                    "approved_finding_ids": ruling.approved_finding_ids,
                },
            )
        )
        run.debate_history = debate_result.history

        removed_ids: set[str] = set()
        added_evidence_by_agent: dict[str, list[str]] = {}
        for revision in debate_result.revisions:
            agent_name = revision.get("agent_name")
            for finding_id in revision.get("removed_finding_ids", []) or []:
                removed_ids.add(str(finding_id))
            if agent_name and revision.get("added_evidence_ids"):
                added_evidence_by_agent.setdefault(str(agent_name), []).extend(
                    str(evidence_id) for evidence_id in revision["added_evidence_ids"]
                )

        if removed_ids:
            run.findings = [
                finding for finding in run.findings if finding.finding_id not in removed_ids
            ]
            removed_evidence_ids = {
                item.evidence_id for item in run.evidence if item.finding_id in removed_ids
            }
            for item in run.evidence:
                if item.finding_id in removed_ids:
                    item.verifier_status = "rejected_by_debate"
            for module in run.module_results:
                module["finding_ids"] = [
                    fid
                    for fid in module.get("finding_ids", [])
                    if str(fid) not in removed_ids
                ]
                module["evidence_ids"] = [
                    eid
                    for eid in module.get("evidence_ids", [])
                    if str(eid) not in removed_evidence_ids
                ]

        flagged_set = set(ruling.flagged_finding_ids or [])
        for finding in run.findings:
            if finding.finding_id in flagged_set:
                finding.verification_status = "needs_human_review"

        approved_set = set(ruling.approved_finding_ids or [])
        for finding in run.findings:
            if finding.finding_id in approved_set:
                try:
                    finding.confidence = min(1.0, float(getattr(finding, "confidence", 0.5)) + 0.2)
                except (TypeError, ValueError):
                    pass
                if finding.verification_status != "approved":
                    finding.verification_status = "approved"

        existing_evidence_ids = {evidence.evidence_id for evidence in run.evidence}
        for agent_name, evidence_ids in added_evidence_by_agent.items():
            for finding in run.findings:
                if finding.agent_name == agent_name:
                    for evidence_id in evidence_ids:
                        if evidence_id not in finding.evidence_ids:
                            finding.evidence_ids.append(evidence_id)
            for evidence_id in evidence_ids:
                if evidence_id not in existing_evidence_ids:
                    supplemental = self._retrieve_supplemental_evidence(
                        run,
                        eid=evidence_id,
                        agent_name=agent_name,
                    )
                    run.evidence.extend(supplemental)
                    existing_evidence_ids.update(evidence.evidence_id for evidence in supplemental)

        if ruling.decision == "request_supplement":
            run = self._run_supplement_loop(run, list(ruling.required_supplements or []))
        elif ruling.decision == "escalate_human":
            run.status = "awaiting_human_review"
            run.current_stage = "expert_debate"
            run.events.append(
                _event(
                    "run_paused_for_human",
                    "orchestrator",
                    "运行暂停 — 等待人工复核",
                    f"红队仲裁：{ruling.reasoning}",
                    stage_key="expert_debate",
                    stage_status="waiting_user",
                    action_label="人工接管",
                    payload={"decision": ruling.decision, "reasoning": ruling.reasoning},
                )
            )
            self._save_run(run)
            return run

        run.events.append(
            _event(
                "debate_completed",
                "orchestrator",
                "专家辩论阶段完成",
                f"仲裁决议：{ruling.decision}；保留 {len(run.findings)} 条 finding 进入交叉验证。",
                stage_key="expert_debate",
                stage_status="completed",
                action_label="辩论结束",
                payload={
                    "decision": ruling.decision,
                    "finding_count": len(run.findings),
                },
            )
        )
        self._save_run(run)
        return run

    def _save_run(self, run: AnalysisRun) -> None:
        dumped = run.model_dump()
        dumped["input_payload"] = _payload_from_run(run).model_dump()
        dumped["frozen_rule_snapshot"] = run.frozen_rule_snapshot
        dumped["evidence"] = [item.model_dump() for item in run.evidence]
        dumped["verification_results"] = [item.model_dump() for item in run.verification_results]
        dumped["report"] = run.report.model_dump() if run.report else None
        previous_event_count = self._published_event_counts.get(run.run_id)
        if self.use_database:
            dumped["document_blocks"] = [item.model_dump() for item in run.document_blocks]
            with self.session_factory() as session:
                row = session.get(AnalysisRunRow, run.run_id)
                if row is None:
                    if previous_event_count is None:
                        previous_event_count = 0
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
                    if previous_event_count is None:
                        previous_event_count = len((row.payload or {}).get("events") or [])
                    row.company_name = run.company_name
                    row.status = run.status
                    row.current_stage = run.current_stage
                    row.completed_at = run.completed_at
                    row.inputs_frozen = run.inputs_frozen
                    row.payload = dumped
                session.commit()
            self._publish_new_events(run, previous_event_count)
            return
        with _path_lock(self.path):
            data = _read_yaml_unlocked(self.path, {})
            existing = data.get(run.run_id, {})
            if previous_event_count is None:
                previous_event_count = len(existing.get("events") or [])
            existing_blocks = existing.get("document_blocks") or []
            if existing_blocks and len(existing_blocks) == len(run.document_blocks):
                dumped["document_blocks"] = existing_blocks
            else:
                dumped["document_blocks"] = [item.model_dump() for item in run.document_blocks]
            data[run.run_id] = dumped
            _write_yaml_atomic_unlocked(self.path, data)
        self._publish_new_events(run, previous_event_count)

    def _publish_new_events(self, run: AnalysisRun, previous_event_count: int) -> None:
        events = run.events[previous_event_count:]
        self._published_event_counts[run.run_id] = len(run.events)
        if not events:
            return
        try:
            from riskagent.runtime_events import get_event_broker

            broker = get_event_broker()
            for event in events:
                broker.publish_sync(run.run_id, event)
        except Exception:
            return

    def _publish_event(self, run: AnalysisRun, event: dict) -> None:
        """Persist an intermediate event so API and worker processes agree."""

        event.setdefault("event_id", f"evt_{uuid.uuid4().hex}")
        event.setdefault("created_at", _now())
        run.events = _merge_events(run.events, [event])
        self._save_run(run)

    def _load_all(self) -> dict[str, Any]:
        with _path_lock(self.path):
            return _read_yaml_unlocked(self.path, {})

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
        with _path_lock(self.evidence_library_path):
            return _read_yaml_unlocked(self.evidence_library_path, [])

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
        with _path_lock(self.evidence_library_path):
            _write_yaml_atomic_unlocked(self.evidence_library_path, items)


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
        questions.append("请确认是否使用默认风险规则和默认分析深度；专家编队将由战略层动态生成。")
    return questions


def _validate_rule_set(payload: AnalysisRunCreate, snapshot: dict[str, Any]) -> None:
    if payload.risk_rule_set_id and payload.risk_rule_set_id != snapshot["rule_set_id"]:
        raise ValueError("Only the active risk rule set is available for analysis.")


def _analysis_config(payload: AnalysisRunCreate, agent_options: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "risk_rule_set_id": payload.risk_rule_set_id or "active",
        "analysis_depth": payload.analysis_depth,
        "selection_mode": "dynamic_strategy",
        "available_agent_count": len(agent_options),
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
        relevant = [item for item in linked if item.relevance_score >= 0.50]
        strong = [item for item in linked if item.relevance_score >= 0.68]
        if strong:
            status = "supported"
            reason = "风险判断绑定了至少一条通过规则相关性门禁的强证据。"
        elif relevant:
            status = "weakly_supported"
            reason = "原文证据通过最低相关性门禁，但仍需人工复核。"
        elif linked:
            status = "needs_human_review"
            reason = "检索到候选原文，但规则相关性未达到 0.50 证据门禁。"
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


def _document_block_stats(blocks: list[DocumentBlock]) -> dict[str, int]:
    risk_terms = [
        "风险",
        "现金流",
        "客户",
        "供应商",
        "关联交易",
        "诉讼",
        "合规",
        "毛利率",
        "破发",
        "估值",
        "redemption",
        "cash flow",
        "litigation",
    ]
    return {
        "text_blocks": sum(1 for block in blocks if block.block_type == "text"),
        "table_blocks": sum(1 for block in blocks if block.block_type == "table"),
        "image_blocks": sum(1 for block in blocks if block.block_type == "image_ocr"),
        "risk_tagged_blocks": sum(
            1
            for block in blocks
            if any(term.lower() in block.text.lower() for term in risk_terms)
        ),
    }


def _dedupe_evidence(evidence: list[RunEvidence]) -> list[RunEvidence]:
    seen: set[tuple[str, str]] = set()
    deduped: list[RunEvidence] = []
    for item in evidence:
        key = (item.finding_id, item.source_hash or item.document_block_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _chunk_evidence_candidate(item: ChunkSearchResult) -> dict[str, Any]:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    region = next(
        (
            candidate
            for candidate in item.merged_bboxes
            if isinstance(candidate, dict)
            and int(candidate.get("page_idx", -1)) == item.page_idx
        ),
        {},
    )
    return {
        "block_id": item.chunk_id,
        "document_id": item.document_id,
        "page_number": item.page_idx,
        "section_path": str(metadata.get("section_path") or item.chunk_type),
        "text": item.text,
        "bbox": [float(value) for value in region.get("bbox", [])],
        "bbox_coordinate_space": str(
            metadata.get("bbox_coordinate_space") or "pdf_points"
        ),
        "char_start": int(metadata.get("char_start") or 0),
        "char_end": int(metadata.get("char_end") or 0),
        "source_hash": str(
            metadata.get("source_hash")
            or hashlib.sha256(item.text.encode("utf-8")).hexdigest()
        ),
        "retrieval_score": item.score,
        "vector_score": item.vector_score,
        "text_score": item.text_score,
        "score": item.score,
    }


def _candidate_excerpt(candidate: dict[str, Any], *, limit: int) -> str:
    """Return the passage around the matched rule anchor, not an unrelated prefix."""

    text = str(candidate.get("text") or "")
    anchors = [str(item) for item in candidate.get("matched_anchors") or [] if item]
    positions = [text.lower().find(anchor.lower()) for anchor in anchors]
    positions = [position for position in positions if position >= 0]
    if not positions or len(text) <= limit:
        return text[:limit]
    center = min(positions)
    start = max(0, center - limit // 3)
    end = min(len(text), start + limit)
    start = max(0, end - limit)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"


def _block_candidates_for_finding(
    finding: RiskFinding,
    blocks: list[DocumentBlock],
) -> list[dict[str, Any]]:
    terms = [
        term
        for term in [finding.category, finding.title, *finding.matched_terms]
        if term
    ]
    candidates = []
    for block in blocks:
        text = block.text.lower()
        hits = [term for term in terms if term.lower() in text]
        if not hits:
            continue
        score = min(0.95, 0.62 + len(set(hits)) * 0.08)
        candidates.append(
            {
                "block_id": block.block_id,
                "document_id": block.document_id,
                "page_number": block.page_number,
                "section_path": block.section_path,
                "text": block.text,
                "bbox": block.bbox,
                "bbox_coordinate_space": block.bbox_coordinate_space,
                "char_start": block.char_start,
                "char_end": block.char_end,
                "source_hash": block.source_hash,
                "retrieval_score": 0.0,
                "relevance_score": score,
                "score": score,
            }
        )
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates


def _apply_verification(
    findings: list[RiskFinding],
    evidence: list[RunEvidence],
    results: list[VerificationResult],
) -> None:
    status_by_finding = {item.finding_id: item.status for item in results}
    for finding in findings:
        status = status_by_finding.get(finding.finding_id, "needs_human_review")
        linked_relevance = [
            item.relevance_score
            for item in evidence
            if item.finding_id == finding.finding_id
        ]
        strongest_evidence = max(linked_relevance, default=0.0)
        expert_confidence = max(0.0, min(1.0, finding.confidence))
        calibrated = 0.55 * expert_confidence + 0.45 * strongest_evidence
        if status == "supported":
            finding.confidence = max(0.55, min(0.95, calibrated))
        elif status == "weakly_supported":
            finding.confidence = max(0.50, min(0.69, calibrated))
        else:
            finding.confidence = min(0.49, calibrated)
        finding.verification_status = status
        finding.requires_human_review = status in {"weakly_supported", "needs_human_review"}
    for item in evidence:
        if item.verifier_status == "rejected_by_debate":
            continue
        item.verifier_status = status_by_finding.get(item.finding_id, "needs_human_review")


def _quality_review(run: AnalysisRun) -> dict[str, Any]:
    evidence_by_id = {item.evidence_id: item for item in run.evidence}
    issues: list[str] = []
    admitted = {
        item.finding_id
        for item in run.verification_results
        if item.status in {"supported", "weakly_supported"}
    }
    for finding in run.findings:
        linked = [evidence_by_id[evidence_id] for evidence_id in finding.evidence_ids if evidence_id in evidence_by_id]
        if not linked:
            issues.append(f"{finding.finding_id} / {finding.title} 缺少 PDF 原文证据。")
            continue
        if any(item.page_number <= 0 or not item.bbox or not item.source_hash for item in linked):
            issues.append(f"{finding.finding_id} / {finding.title} 的页码、bbox 或 source_hash 不完整。")
        if finding.severity == "high" and all(item.verifier_status != "supported" for item in linked):
            issues.append(f"{finding.finding_id} / {finding.title} 为高风险但缺少 supported 证据。")
    return {
        "review_status": (
            "draft"
            if admitted and not issues
            else "no_verified_findings"
            if not admitted and not issues
            else "needs_human_review"
        ),
        "issues": issues,
    }


def _empty_verified_report(
    run: AnalysisRun,
    module_results: list[dict[str, Any]],
) -> IntegratedRiskReport:
    """Produce a truthful zero-finding report without asking the LLM to infer risk."""

    return IntegratedRiskReport(
        title=f"{run.company_name} IPO 风险分析报告",
        executive_summary=(
            "本次分析未形成通过证据门禁的风险结论。该结果表示现有检索与验证材料"
            "不足以支持正式判断，不表示相关风险不存在。"
        ),
        overall_risk_level="未评级（无已验证结论）",
        sections=[
            {
                "module_id": str(module["module_id"]),
                "title": str(module.get("module") or module["module_id"]),
                "analysis": "未形成通过证据门禁的风险结论，需补充定向检索或人工复核。",
                "finding_ids": [],
                "evidence_ids": [],
            }
            for module in module_results
        ],
        recommendations=["针对未充分覆盖的规则执行定向补证和人工复核。"],
        limitations=["无已验证风险结论，不能据此推断企业不存在相关风险。"],
    )


def _render_report_header(run: AnalysisRun) -> str:
    lines = [
        f"# {run.company_name} IPO 风险分析草案",
        "",
        f"- 数据截止日期：{run.data_cutoff_date}",
        f"- 规则版本：{run.rule_snapshot['rule_set_id']} / {run.rule_snapshot['version']}",
        "- 复核状态：待质检",
        f"- 核心风险判断：{len(_report_findings(run))} 条",
        f"- PDF 原文证据：{len(run.evidence)} 条",
        "",
        "## 公司基本信息",
        "",
        f"- 公司名称：{run.company_name}",
        "- 上市市场：港股 IPO（基于用户输入与默认分析任务识别）",
        "- 招股书类型：招股书 / 申请版本材料（以上传文件为准）",
        "",
        "## 招股书关键信息摘要",
        "",
        f"- 已解析原文块：{len(run.document_blocks)} 个",
        f"- 覆盖章节：{', '.join(sorted({block.section_path for block in run.document_blocks})[:8]) or '待识别'}",
        "- 后续判断均要求回溯至 document_blocks、页码、bbox 与 source_hash。",
        "",
    ]
    return "\n".join(lines)


def _render_core_findings_section(run: AnalysisRun, report_findings: list[RiskFinding]) -> str:
    evidence_by_id = {item.evidence_id: item for item in run.evidence}
    status_by_finding = {item.finding_id: item.status for item in run.verification_results}
    lines = [
        "## 核心风险结论",
    ]
    for finding in report_findings:
        status = status_by_finding.get(finding.finding_id, "needs_human_review")
        refs = [
            evidence_by_id[evidence_id]
            for evidence_id in finding.evidence_ids
            if evidence_id in evidence_by_id
        ]
        ref_text = "；".join(
            f"{item.evidence_id}（第 {item.page_number} 页，{item.section_path}）"
            for item in refs[:3]
        ) or "等待人工补证"
        lines.extend(
            [
                "",
                f"### {finding.title}",
                f"- 规则：{finding.rule_id}",
                f"- Skill：{finding.skill_id or '未标注'}",
                f"- 严重程度：{finding.severity}",
                f"- 证据门禁：{status}",
                f"- 证据引用：{ref_text}",
                f"- 摘要：{finding.reasoning_summary}",
            ]
        )
    if not report_findings:
        lines.append("未发现满足证据门禁的关键风险结论。")
    return "\n".join(lines)


def _render_report_footer(run: AnalysisRun, report_findings: list[RiskFinding]) -> str:
    lines = [
        "",
        "## 风险等级",
        "",
        f"- 综合风险等级：{_overall_risk_level(report_findings)}",
        "- 等级口径：依据触发规则数量、严重程度、证据强度和是否需要人工复核综合判断。",
        "",
        "## 证据引用",
        "",
    ]
    for item in run.evidence[:30]:
        lines.append(f"- {item.evidence_id}：第 {item.page_number} 页 / {item.section_path} / 证据相关性 {item.relevance_score:.2f} / {item.text[:120]}")
    lines.extend(
        [
            "",
            "## 总结建议",
            "",
            "建议在正式投委会或上市风险评审前，对 weakly_supported 与 needs_human_review 判断进行人工复核，并重点核对现金流、客户供应商集中、关联交易、诉讼监管和发行估值相关章节。",
        ]
    )
    return "\n".join(lines)


def _report_findings(run: AnalysisRun) -> list[RiskFinding]:
    allowed = {"supported", "weakly_supported"}
    status_by_finding = {item.finding_id: item.status for item in run.verification_results}
    return [
        finding
        for finding in run.findings
        if status_by_finding.get(finding.finding_id) in allowed
    ]


async def _build_report_streaming(run: AnalysisRun) -> AsyncIterator[str]:
    """Stream the report body: deterministic header/footer + LLM-generated section bodies."""
    report_findings = _report_findings(run)
    yield _render_report_header(run)
    yield _render_core_findings_section(run, report_findings)
    grouped: dict[str, list[RiskFinding]] = {}
    for finding in report_findings:
        grouped.setdefault(_report_section_for_finding(finding), []).append(finding)
    evidence_by_id = {item.evidence_id: item for item in run.evidence}
    section_titles = [
        "财务风险分析",
        "业务风险分析",
        "行业与竞争风险分析",
        "合规风险分析",
        "估值与破发风险分析",
    ]
    system_prompt = REPORT_SYSTEM_PROMPT.format(company_name=run.company_name)
    for section in section_titles:
        yield f"\n\n## {section}\n\n"
        section_findings = grouped.get(section, [])
        if not section_findings:
            yield "本轮未形成该维度的高置信结构化风险判断，建议人工复核招股书相关章节。\n"
            continue
        user_payload = json.dumps(
            {
                "section": section,
                "company_name": run.company_name,
                "findings": [finding.model_dump() for finding in section_findings],
                "evidence": [
                    evidence_by_id[evidence_id].model_dump()
                    for finding in section_findings
                    for evidence_id in finding.evidence_ids
                    if evidence_id in evidence_by_id
                ],
            },
            ensure_ascii=False,
        )
        from riskagent.runtime_events import get_event_broker

        broker = get_event_broker()
        await broker.publish(
            run.run_id,
            _event(
                "report_section_started",
                "report_writer",
                f"{section}生成开始",
                f"准备生成 {section} 正文。",
                stage_key="report_generation",
                action_label="章节生成开始",
                payload={
                    "section": section,
                    "finding_count": len(section_findings),
                },
            ),
        )
        try:
            async for delta in get_default_llm_client().chat_stream(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_payload},
                ],
                temperature=0.3,
            ):
                yield delta
            await broker.publish(
                run.run_id,
                _event(
                    "report_section_completed",
                    "report_writer",
                    f"{section}生成完成",
                    f"已完成 {section} 正文生成。",
                    stage_key="report_generation",
                    stage_status="completed",
                    action_label="章节生成完成",
                    payload={
                        "section": section,
                        "finding_count": len(section_findings),
                    },
                ),
            )
        except Exception as exc:
            await broker.publish(
                run.run_id,
                _event(
                    "report_section_failed",
                    "report_writer",
                    f"{section}生成失败",
                    f"LLM 流式生成失败：{type(exc).__name__}: {exc}",
                    stage_key="report_generation",
                    stage_status="failed",
                    action_label="章节生成失败",
                    payload={
                        "section": section,
                        "finding_count": len(section_findings),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                ),
            )
            yield f"\n\n（LLM 流式生成失败：{type(exc).__name__}: {exc}，已降级为模板输出。）\n\n"
            for finding in section_findings:
                refs = [
                    evidence_by_id[evidence_id]
                    for evidence_id in finding.evidence_ids
                    if evidence_id in evidence_by_id
                ]
                first_ref = refs[0] if refs else None
                yield (
                    f"- {finding.title}：{finding.reasoning_summary}"
                    + (f"（证据：第 {first_ref.page_number} 页，{first_ref.section_path}）" if first_ref else "（证据：待补充）")
                    + "\n"
                )
    yield _render_report_footer(run, report_findings)


async def _collect_report_stream(run: AnalysisRun) -> str:
    return "".join([chunk async for chunk in _build_report_streaming(run)])


def _build_report(run: AnalysisRun) -> ReportDraft:
    markdown = asyncio.run(_collect_report_stream(run))
    html = markdown.replace("\n", "<br>")
    return ReportDraft(
        report_id=f"report_{uuid.uuid4().hex[:10]}",
        markdown=markdown,
        html=html,
        created_at=_now(),
    )


def _build_integrated_report(run: AnalysisRun, integrated: IntegratedRiskReport) -> ReportDraft:
    """Render a validated strategic report without allowing new evidence claims."""

    evidence_by_id = {item.evidence_id: item for item in run.evidence}
    report_evidence = [
        item
        for item in run.evidence
        if item.verifier_status in {"supported", "weakly_supported"}
    ]
    lines = [
        f"# {integrated.title}",
        "",
        f"- 公司：{run.company_name}",
        f"- 数据截止日期：{run.data_cutoff_date}",
        f"- 规则版本：{run.rule_snapshot['rule_set_id']} / {run.rule_snapshot['version']}",
        f"- 综合风险等级：{integrated.overall_risk_level}",
        "- 复核状态：待质检",
        "",
        "## 执行摘要",
        "",
        integrated.executive_summary,
    ]
    for section in integrated.sections:
        lines.extend(["", f"## {section.title}", "", section.analysis])
        if section.finding_ids:
            lines.append(f"- 风险判断：{', '.join(section.finding_ids)}")
        if section.evidence_ids:
            refs = []
            for evidence_id in section.evidence_ids:
                item = evidence_by_id[evidence_id]
                refs.append(
                    f"{evidence_id}（第 {item.page_number} 页，{item.section_path}）"
                )
            lines.append(f"- 证据引用：{'；'.join(refs)}")
    if integrated.cross_module_risks:
        lines.extend(["", "## 跨模块风险关联"])
        for item in integrated.cross_module_risks:
            lines.extend(["", f"### {item.title}", item.analysis])
            if item.finding_ids:
                lines.append(f"- 关联判断：{', '.join(item.finding_ids)}")
            if item.evidence_ids:
                lines.append(f"- 关联证据：{', '.join(item.evidence_ids)}")
    lines.extend(["", "## 建议"])
    lines.extend(f"- {item}" for item in integrated.recommendations)
    if integrated.limitations:
        lines.extend(["", "## 局限与人工复核"])
        lines.extend(f"- {item}" for item in integrated.limitations)
    pending_review = {
        item.finding_id
        for item in run.verification_results
        if item.status == "needs_human_review"
    }
    if pending_review:
        lines.extend(["", "## 待人工补证事项"])
        for finding in run.findings:
            if finding.finding_id in pending_review:
                lines.append(
                    f"- {finding.finding_id} / {finding.title}：未通过证据门禁，"
                    "不作为正式风险结论。"
                )
    lines.extend(["", "## 证据索引"])
    for item in report_evidence[:50]:
        lines.append(
            f"- {item.evidence_id}：第 {item.page_number} 页 / {item.section_path} / "
            f"证据相关性 {item.relevance_score:.2f} / {item.text[:120]}"
        )
    markdown = "\n".join(lines)
    return ReportDraft(
        report_id=f"report_{uuid.uuid4().hex[:10]}",
        markdown=markdown,
        html=markdown.replace("\n", "<br>"),
        created_at=_now(),
    )


def _report_section_for_finding(finding: RiskFinding) -> str:
    if finding.skill_id == "financial_risk_skill" or finding.rule_type == "quantitative_financial":
        return "财务风险分析"
    if finding.skill_id == "legal_compliance_skill":
        return "合规风险分析"
    if finding.skill_id == "market_valuation_skill" or finding.category in {"行业竞争", "估值", "发行定价", "破发风险"}:
        return "估值与破发风险分析"
    return "业务风险分析"


def _report_sections(markdown: str) -> list[dict[str, str]]:
    """Split a report markdown into `## `-level sections for archival and indexing."""

    sections: list[dict[str, str]] = []
    title = "报告概述"
    buffer: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if buffer and "".join(buffer).strip():
                sections.append({"title": title, "text": "\n".join(buffer).strip()})
            title = line[3:].strip() or title
            buffer = []
            continue
        buffer.append(line)
    if buffer and "".join(buffer).strip():
        sections.append({"title": title, "text": "\n".join(buffer).strip()})
    return sections


def _overall_risk_level(findings: list[RiskFinding]) -> str:
    if any(finding.severity == "high" and not finding.requires_human_review for finding in findings):
        return "高"
    if len(findings) >= 3:
        return "中"
    return "低" if findings else "待判断"


def _build_trace_view(run: AnalysisRun) -> AnalysisTrace:
    stages = [
        TraceStage(key=key, label=label, owner=owner, status=_stage_status(run, key))
        for key, label, owner in PIPELINE_STAGES
    ]
    flow = [_trace_event(run.created_at, event) for event in run.events]
    evidence = [
        TraceEvidenceItem(
            id=item.evidence_id,
            source="官方",
            title=f"{item.section_path} P.{item.page_number}",
            conf=round(item.relevance_score, 4),
            time="近实时",
            page=item.page_number,
            snippet=item.text,
            document_id=item.document_id or None,
            bbox=item.bbox,
        )
        for item in run.evidence[:20]
    ]
    return AnalysisTrace(stages=stages, seed=flow[:8], flow=flow, evidenceFeed=evidence)


def _build_analysis_progress(run: AnalysisRun) -> AnalysisProgress:
    stored = run.progress or {}
    pdf_data = dict(stored.get("pdf") or {})
    index_data = dict(stored.get("index") or {})
    if not pdf_data or pdf_data.get("status") == "pending":
        parsed_pages = max((item.page_number for item in run.document_blocks), default=0)
        if parsed_pages:
            pdf_data = {
                "status": "completed",
                "parsed_pages": parsed_pages,
                "total_pages": parsed_pages,
                "parsed_blocks": len(run.document_blocks),
            }
            index_data = {
                "status": "completed" if run.status == "completed" else "pending",
                "indexed_blocks": len(run.document_blocks) if run.status == "completed" else 0,
                "total_blocks": len(run.document_blocks),
            }
    pdf = PdfProgress.model_validate(pdf_data)
    index = IndexProgress.model_validate(index_data)
    assignments = run.strategy_plan.get("assignments") or []
    team_state = {
        str(item.get("agent_name") or ""): str(item.get("status") or "")
        for item in run.team_state
    }
    agents = []
    for assignment in assignments:
        agent_name = str(assignment.get("agent_name") or "")
        if not agent_name:
            continue
        events = [event for event in run.events if event.get("actor_name") == agent_name]
        latest = events[-1] if events else {}
        agents.append(
            AgentCollaborationProgress(
                agent_name=agent_name,
                module_id=str(assignment.get("module_id") or ""),
                module=str(assignment.get("module") or ""),
                status=_agent_progress_status(
                    run.status,
                    team_state.get(agent_name, ""),
                    str(latest.get("event_type") or ""),
                ),
                latest_action=str(latest.get("action_label") or latest.get("title") or "待编排"),
                latest_summary=str(latest.get("summary") or "等待战略层分配任务。"),
            )
        )
    return AnalysisProgress(
        run_id=run.run_id,
        phase=str(stored.get("phase") or run.current_stage),
        updated_at=str(stored.get("updated_at") or run.created_at),
        status=run.status,
        current_stage=run.current_stage,
        error_summary=run.error_summary,
        pdf=pdf,
        index=index,
        agents=agents,
    )


def _agent_progress_status(run_status: str, team_status: str, event_type: str) -> str:
    state = team_status.lower()
    event = event_type.lower()
    if "failed" in state or "failed" in event:
        return "failed"
    if "completed" in state or "finished" in state or "completed" in event:
        return "completed"
    if "debate" in event or "revised" in event or "revision" in event:
        return "debating"
    if "retriev" in event or "evidence" in event:
        return "retrieving_evidence"
    if "started" in event or "running" in state or "work" in state or "llm" in event:
        return "analyzing"
    if run_status in {"expert_orchestration", "awaiting_expert_selection"}:
        return "initializing"
    return "queued"


def _stage_status(run: AnalysisRun, key: str) -> str:
    order = [item[0] for item in PIPELINE_STAGES]
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
    if event_type in {"agent_started", "stage_started", "tool_called", "tool_started", "rules_assigned"}:
        return "execute"
    if event_type in {"agent_completed", "stage_completed", "report_section_generated", "evidence_found", "risk_finding_created", "evidence_deduped"}:
        return "discover"
    if event_type in {"verification_completed", "risk_finding_verified", "qa_passed"}:
        return "verdict"
    if event_type in {"stage_failed", "qa_issue_found"}:
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


def _skill_id_for_evaluation(item: RuleEvaluation) -> str | None:
    try:
        rule = RiskRuleRepository().get_rule(item.rule_id)
    except Exception:
        return None
    return skill_for_rule(rule).skill_id


def _event(
    event_type: str,
    actor_name: str,
    title: str,
    summary: str,
    **extra: Any,
) -> dict[str, Any]:
    event = {
        "event_id": f"evt_{uuid.uuid4().hex}",
        "event_type": event_type,
        "actor_name": actor_name,
        "title": title,
        "summary": summary,
        "created_at": _now(),
    }
    event.update({key: value for key, value in extra.items() if value is not None})
    return event


def _merge_events(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = list(existing)
    seen = {
        (
            item.get("event_id") or item.get("created_at"),
            item.get("event_type"),
            item.get("actor_name"),
            item.get("title"),
        )
        for item in existing
    }
    for item in incoming:
        key = (
            item.get("event_id") or item.get("created_at"),
            item.get("event_type"),
            item.get("actor_name"),
            item.get("title"),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _resolve_provider_result(value: Any) -> Any:
    return asyncio.run(value) if inspect.isawaitable(value) else value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve() if path.exists() else path.absolute())
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _FILE_LOCKS[key] = lock
        return lock


def _read_yaml_unlocked(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return yaml.safe_load(path.read_text(encoding="utf-8")) or default


def _write_yaml_atomic_unlocked(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    temp_path.replace(path)


def _seed_agent_specs() -> list[AgentSpec]:
    expertise_by_persona = {str(item["agent_id"]): item for item in AGENT_EXPERTS}
    return [
        AgentSpec(
            name=str(item["agent_name"]),
            title=str(item["title"]),
            rule_types=tuple(item.get("rule_types") or []),
            categories=tuple(item.get("categories") or []),
            default=bool(item.get("is_default")),
            persona_id=str(item.get("persona_id") or ""),
            display_name=str(item.get("display_name") or ""),
            layer=str(item.get("layer") or ""),
            function=str(item.get("function") or ""),
            avatar=str(expertise_by_persona.get(str(item.get("persona_id") or ""), {}).get("avatar") or ""),
            accent=str(expertise_by_persona.get(str(item.get("persona_id") or ""), {}).get("accent") or ""),
            expertise=tuple(
                expertise_by_persona.get(str(item.get("persona_id") or ""), {}).get("expertise") or []
            ),
            role_zh=str(expertise_by_persona.get(str(item.get("persona_id") or ""), {}).get("role") or ""),
        )
        for item in AGENT_SPECS
    ]


def get_default_analysis_repository() -> AnalysisRunRepository:
    return AnalysisRunRepository()
