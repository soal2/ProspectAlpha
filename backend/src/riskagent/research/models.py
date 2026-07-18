"""Pydantic contracts for the external research / autonomous risk engine (Phase 1).

These models are intentionally independent of the rule track
(``RuleEvaluation`` / ``RiskFinding``): a :class:`RiskHypothesis` is legal
without any ``rule_ids``.  None of the enum values defined here may ever be
written into ``AnalysisRun.status`` — research completion lives in
``ResearchArtifact.completion_status`` only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# Nine baseline due-diligence dimensions.  User focus may re-prioritise them
# but must never remove any of them from a standard/deep baseline plan.
BASELINE_DIMENSIONS: tuple[str, ...] = (
    "财务",
    "业务",
    "治理",
    "法务监管",
    "行业竞争",
    "估值",
    "核心人员",
    "供应链",
    "声誉",
)


class HypothesisStatus(str, Enum):
    discovered = "discovered"
    investigating = "investigating"
    partially_supported = "partially_supported"
    verified = "verified"
    contested = "contested"
    refuted = "refuted"
    inconclusive = "inconclusive"


class RedTeamDecision(str, Enum):
    verified = "verified"
    material_unresolved = "material_unresolved"
    request_supplement = "request_supplement"
    downgraded = "downgraded"
    refuted = "refuted"
    escalate_human = "escalate_human"


class ResearchCompletionStatus(str, Enum):
    not_started = "not_started"
    running = "running"
    complete = "complete"
    external_research_incomplete = "external_research_incomplete"
    failed = "failed"


class ReviewState(str, Enum):
    draft = "draft"
    auto_completed = "auto_completed"
    review_required = "review_required"
    human_revised = "human_revised"


class ExternalSearchPolicy(str, Enum):
    disabled = "disabled"
    public_only = "public_only"
    approved_sensitive = "approved_sensitive"


class SourceTier(str, Enum):
    tier1 = "tier1"
    tier2 = "tier2"
    tier3 = "tier3"
    unknown = "unknown"


class SourceCard(BaseModel):
    evidence_id: str = Field(default_factory=lambda: _new_id("src"))
    url: str
    final_url: Optional[str] = None
    title: Optional[str] = None
    site_name: Optional[str] = None
    origin: Literal["search_snippet", "fetched_page"]
    snippet: Optional[str] = None
    extract: Optional[str] = None
    content_hash: Optional[str] = None
    tier: SourceTier = SourceTier.unknown
    tier_reason: Optional[str] = None
    independence_cluster: Optional[str] = None
    accessible: bool = True
    inaccessible_reason: Optional[str] = None
    published_at: Optional[str] = None
    event_date: Optional[str] = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    undated: bool = False
    is_post_prospectus_update: bool = False
    language: Optional[str] = None
    tool_call_id: Optional[str] = None
    hypothesis_id: Optional[str] = None
    coverage_task_id: Optional[str] = None
    selection: Literal["candidate", "selected", "excluded"] = "candidate"
    selection_reason: Optional[str] = None


class GateResult(BaseModel):
    passed: bool
    tier1_count: int = 0
    independent_tier2_clusters: int = 0
    refutation_search_done: bool = False
    blocking: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class ConfidenceComponent(BaseModel):
    name: Literal[
        "source_authority",
        "independence",
        "consistency",
        "counter_evidence",
        "freshness",
        "coverage",
        "agent_disagreement",
    ]
    score: float = Field(ge=0.0, le=1.0)
    reason: str


class ConfidenceAssessment(BaseModel):
    level: Literal["high", "medium", "low"]
    components: list[ConfidenceComponent] = Field(default_factory=list)
    # Recorded verbatim for audit, carries zero weight in the level computation.
    llm_self_reported: Optional[float | str] = None


class PlannedQuery(BaseModel):
    text: str
    language: Literal["zh-Hans", "zh-Hant", "en"] = "zh-Hans"
    freshness: Optional[str] = None
    canonical_form: str


class CoverageTask(BaseModel):
    coverage_task_id: str = Field(default_factory=lambda: _new_id("cov"))
    dimension: str
    module_id: Optional[str] = None
    priority: Literal["high", "medium", "low"] = "medium"
    queries: list[PlannedQuery] = Field(default_factory=list)
    status: Literal["pending", "in_progress", "done", "skipped", "blocked"] = "pending"
    triggered_hypothesis_ids: list[str] = Field(default_factory=list)

    @field_validator("dimension")
    @classmethod
    def _dimension_known(cls, value: str) -> str:
        if value not in BASELINE_DIMENSIONS:
            raise ValueError(f"unknown dimension: {value!r}")
        return value


class RiskHypothesis(BaseModel):
    hypothesis_id: str = Field(default_factory=lambda: _new_id("hyp"))
    title: str
    statement: str
    anomaly_signals: list[str] = Field(default_factory=list)
    causal_chain: Optional[str] = None
    potential_impact: Optional[str] = None
    dimension: str
    module_id: Optional[str] = None
    agent_name: Optional[str] = None
    origin: Literal["baseline_plan", "anomaly", "gap", "user_focus", "red_team"]
    # Optional by design: hypotheses without any rule linkage are first-class.
    rule_ids: list[str] = Field(default_factory=list)
    importance: Literal["high", "medium", "low"] = "medium"
    time_sensitive: bool = False
    investigation_plan: Optional[str] = None
    refutation_criteria: Optional[str] = None
    status: HypothesisStatus = HypothesisStatus.discovered
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    counter_evidence_ids: list[str] = Field(default_factory=list)
    refutation_search_done: bool = False
    gate: Optional[GateResult] = None
    confidence: Optional[ConfidenceAssessment] = None
    residual_uncertainty: Optional[str] = None
    review_state: ReviewState = ReviewState.draft
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("dimension")
    @classmethod
    def _dimension_known(cls, value: str) -> str:
        if value not in BASELINE_DIMENSIONS:
            raise ValueError(f"unknown dimension: {value!r}")
        return value


class HypothesisTransition(BaseModel):
    transition_id: str = Field(default_factory=lambda: _new_id("trn"))
    hypothesis_id: str
    from_status: HypothesisStatus
    to_status: HypothesisStatus
    at: datetime = Field(default_factory=utc_now)
    actor: Literal["engine", "red_team", "system", "human"]
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    tool_call_ids: list[str] = Field(default_factory=list)
    gate_snapshot: Optional[GateResult] = None


class InjectedContext(BaseModel):
    """Server-injected trusted scope.  Never constructed from model output."""

    run_id: str
    data_cutoff_date: Optional[str] = None
    policy: ExternalSearchPolicy = ExternalSearchPolicy.public_only


class ToolAuditRecord(BaseModel):
    call_id: str = Field(default_factory=lambda: _new_id("call"))
    tool: str
    requested: dict[str, Any] = Field(default_factory=dict)
    rejected_params: dict[str, Any] = Field(default_factory=dict)
    injected: InjectedContext
    hypothesis_id: Optional[str] = None
    coverage_task_id: Optional[str] = None
    cache_hit: bool = False
    dedup_of: Optional[str] = None
    domain_filter_mode: Optional[str] = None
    started_at: datetime = Field(default_factory=utc_now)
    duration_ms: Optional[int] = None
    outcome: Literal["ok", "error", "rejected"] = "ok"
    error_kind: Optional[str] = None
    result_count: Optional[int] = None
    http_status: Optional[int] = None
    final_url: Optional[str] = None
    bytes_read: Optional[int] = None


class RedTeamVerdict(BaseModel):
    verdict_id: str = Field(default_factory=lambda: _new_id("rtv"))
    hypothesis_id: str
    decision: RedTeamDecision
    rationale: str
    counter_evidence_ids: list[str] = Field(default_factory=list)
    tool_call_ids: list[str] = Field(default_factory=list)
    reviewer: str = "zhou_shenyan_redteam_agent"
    created_at: datetime = Field(default_factory=utc_now)


class FrozenArtifactError(RuntimeError):
    pass


class ResearchArtifact(BaseModel):
    artifact_id: str = Field(default_factory=lambda: _new_id("art"))
    version: int = Field(default=1, ge=1)
    based_on_version: Optional[int] = None
    created_at: datetime = Field(default_factory=utc_now)
    frozen_at: Optional[datetime] = None
    frozen: bool = False
    completion_status: ResearchCompletionStatus = ResearchCompletionStatus.not_started
    policy: ExternalSearchPolicy = ExternalSearchPolicy.public_only
    plan: list[CoverageTask] = Field(default_factory=list)
    hypotheses: list[RiskHypothesis] = Field(default_factory=list)
    evidence: list[SourceCard] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def __setattr__(self, name: str, value: Any) -> None:
        if self.__dict__.get("frozen", False):
            raise FrozenArtifactError(
                f"artifact {self.__dict__.get('artifact_id')} v{self.__dict__.get('version')} is frozen"
            )
        super().__setattr__(name, value)

    def freeze(self, now: Optional[datetime] = None) -> "ResearchArtifact":
        """Return a frozen deep-copy snapshot; ``self`` stays mutable."""
        stamp = now or utc_now()
        return self.model_copy(deep=True, update={"frozen": True, "frozen_at": stamp})

    def next_working(self, now: Optional[datetime] = None) -> "ResearchArtifact":
        """New mutable artifact continuing from this version (version + 1)."""
        stamp = now or utc_now()
        return self.model_copy(
            deep=True,
            update={
                "frozen": False,
                "frozen_at": None,
                "version": self.version + 1,
                "based_on_version": self.version,
                "created_at": stamp,
            },
        )

    def get_hypothesis(self, hypothesis_id: str) -> Optional[RiskHypothesis]:
        for hyp in self.hypotheses:
            if hyp.hypothesis_id == hypothesis_id:
                return hyp
        return None

    def get_evidence(self, evidence_id: str) -> Optional[SourceCard]:
        for card in self.evidence:
            if card.evidence_id == evidence_id:
                return card
        return None


class ResearchCheckpoint(BaseModel):
    checkpoint_id: str = Field(default_factory=lambda: _new_id("ckpt"))
    at: datetime = Field(default_factory=utc_now)
    stage: str
    done_task_ids: list[str] = Field(default_factory=list)


class ResearchState(BaseModel):
    """Research sub-state attached to an AnalysisRun.

    ``transitions`` and ``tool_audit`` are append-only cross-version history;
    artifacts are point-in-time snapshots.
    """

    artifact_id: str = Field(default_factory=lambda: _new_id("art"))
    analysis_depth: str = "standard"
    policy: ExternalSearchPolicy = ExternalSearchPolicy.public_only
    versions: list[ResearchArtifact] = Field(default_factory=list)
    working: Optional[ResearchArtifact] = None
    transitions: list[HypothesisTransition] = Field(default_factory=list)
    tool_audit: list[ToolAuditRecord] = Field(default_factory=list)
    red_team_verdicts: list[RedTeamVerdict] = Field(default_factory=list)
    checkpoints: list[ResearchCheckpoint] = Field(default_factory=list)

    @property
    def completion_status(self) -> ResearchCompletionStatus:
        if self.working is not None:
            return self.working.completion_status
        if self.versions:
            return self.versions[-1].completion_status
        return ResearchCompletionStatus.not_started
