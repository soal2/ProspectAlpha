"""Pydantic contracts for the V2 reporting layer.

These models are deliberately independent of the legacy ``ReportDraft``
contract: V2 bundles reference a frozen :class:`ResearchArtifact` (Phase 1)
and never mutate it.  The bundle itself is INSERT-only — every mutation
(annotated, resolved, manually revised, supplemented) produces a new
``report_version`` or a side-table row, never an in-place edit.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .errors import DegradationCode


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Common ref types
# ---------------------------------------------------------------------------


class TemplateRef(BaseModel):
    """Identity of a loaded template (core or fragment).

    ``content_sha256`` is the hash of the loaded .md file body so two bundles
    that claim the same ``template_id/version`` but were rendered against a
    different file are distinguishable.
    """

    template_id: str
    version: int = Field(ge=1)
    content_sha256: str


class ActorRef(BaseModel):
    """Audit actor.  Phase 2 has no auth system; this is a documented placeholder.

    ``actor_kind`` is one of:

    * ``anonymous`` — unauthenticated UI user (current default).
    * ``system`` — pipeline-initiated mutation (e.g. auto-supplement).
    * ``named`` — a non-empty ``actor_id`` was supplied by an out-of-band
      identity layer the reporting code does not own.

    Phase 3+ is expected to replace ``anonymous`` with a real session.
    """

    model_config = ConfigDict(frozen=True)

    actor_kind: Literal["anonymous", "system", "named"] = "anonymous"
    actor_id: str = ""

    def display(self) -> str:
        if self.actor_kind == "named" and self.actor_id:
            return self.actor_id
        return self.actor_kind


class FrozenBundleError(RuntimeError):
    """Raised when a frozen :class:`ReportBundleV2` is mutated in place."""


# ---------------------------------------------------------------------------
# Per-module render output
# ---------------------------------------------------------------------------


class RenderedComponent(BaseModel):
    """A single whitelisted directive rendered against a dataset.

    ``dataset_key`` references a key produced by :class:`DatasetRegistry`;
    ``lineage`` carries the evidence/hypothesis IDs the dataset was derived
    from so the UI can deep-link.
    """

    kind: Literal[
        "risk_profile_heatmap",
        "risk_matrix",
        "risk_card",
        "financial_trend",
        "pipeline_chart",
        "peer_comparison",
        "event_timeline",
        "risk_causal_graph",
        "hypothesis_matrix",
        "source_quality",
        "research_coverage",
    ]
    dataset_key: str
    lineage: list[str] = Field(default_factory=list)
    rendered_html: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class DegradationNote(BaseModel):
    code: DegradationCode
    message_zh: str
    section_id: str | None = None
    detail: str | None = None


class SectionRender(BaseModel):
    """One rendered section of a module — markdown slice + component anchors."""

    section_id: str
    title_zh: str
    markdown: str
    component_anchors: list[str] = Field(default_factory=list)
    referenced_evidence_ids: list[str] = Field(default_factory=list)
    referenced_hypothesis_ids: list[str] = Field(default_factory=list)
    referenced_finding_ids: list[str] = Field(default_factory=list)
    non_formal: bool = False


class ModuleReport(BaseModel):
    """Rendered brief or deep-research module."""

    module_kind: Literal["decision_brief", "deep_research"]
    template: TemplateRef
    sections: list[SectionRender] = Field(default_factory=list)
    components: list[RenderedComponent] = Field(default_factory=list)
    degradations: list[DegradationNote] = Field(default_factory=list)
    markdown: str
    hypothesis_ids_visible: list[str] = Field(default_factory=list)
    finding_ids_visible: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Selection summary (drives brief/deep gates)
# ---------------------------------------------------------------------------


class ExclusionRecord(BaseModel):
    """Why a hypothesis or evidence was excluded from the formal layer.

    ``request_supplement`` verdicts always produce an exclusion (the
    hypothesis is parked pending re-investigation, not surfaced as a formal
    conclusion).  ``tier3_only`` evidence is excluded from the formal layer
    but appears in the quarantined appendix of the deep report.
    """

    target_kind: Literal["hypothesis", "evidence"]
    target_id: str
    reason_code: Literal[
        "request_supplement_verdict",
        "tier3_quarantine",
        "inaccessible_source",
        "out_of_artifact_scope",
    ]
    detail: str = ""


class SelectionSummary(BaseModel):
    """Aggregate counts and ID lists the renderer and gates consume."""

    verified_hypothesis_ids: list[str] = Field(default_factory=list)
    material_unresolved_hypothesis_ids: list[str] = Field(default_factory=list)
    contested_hypothesis_ids: list[str] = Field(default_factory=list)
    partially_supported_hypothesis_ids: list[str] = Field(default_factory=list)
    refuted_hypothesis_ids: list[str] = Field(default_factory=list)
    inconclusive_hypothesis_ids: list[str] = Field(default_factory=list)
    rule_finding_ids: list[str] = Field(default_factory=list)
    exclusions: list[ExclusionRecord] = Field(default_factory=list)
    tier1_evidence_ids: list[str] = Field(default_factory=list)
    tier2_evidence_ids: list[str] = Field(default_factory=list)
    tier3_evidence_ids: list[str] = Field(default_factory=list)
    unknown_tier_evidence_ids: list[str] = Field(default_factory=list)

    @property
    def formal_hypothesis_ids(self) -> list[str]:
        """IDs that may appear in the brief and the deep formal layer."""

        return [
            *self.verified_hypothesis_ids,
            *self.material_unresolved_hypothesis_ids,
        ]


class ReviewTriggerRecord(BaseModel):
    """One fired review trigger.  Empty list means auto-completion is allowed."""

    trigger_name: Literal[
        "external_research_incomplete",
        "red_team_escalate_human",
        "high_importance_nonhigh_confidence",
        "authoritative_source_conflict",
        "major_negative_allegation",
        "listing_eligibility_risk",
        "verdict_status_conflict",
    ]
    detail: str = ""
    hypothesis_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


IndustryKind = Literal["general", "biotech"]


class IndustryProfile(BaseModel):
    detected: IndustryKind = "general"
    fragment: TemplateRef | None = None
    detection_reason: str = "fallback: no industry signal matched"


class ReportBundleV2(BaseModel):
    """A versioned, immutable bundle.

    Mutations never edit a bundle in place: a new ``report_version`` is
    inserted.  Once persisted with ``frozen=True`` the model rejects
    attribute writes (``__setattr__`` raises).
    """

    run_id: str
    report_version: int = Field(ge=1)
    research_artifact_id: str
    research_artifact_version: int = Field(ge=1)
    verdict_snapshot_hash: str
    brief_template: TemplateRef
    deep_template: TemplateRef
    industry_profile: IndustryProfile
    decision_brief: ModuleReport
    deep_research: ModuleReport
    selection: SelectionSummary
    review_state: Literal["draft", "auto_completed", "review_required", "human_revised"]
    review_triggers: list[ReviewTriggerRecord] = Field(default_factory=list)
    degradations: list[DegradationNote] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    frozen: bool = False

    def __setattr__(self, name: str, value: Any) -> None:  # type: ignore[override]
        if self.__dict__.get("frozen", False):
            raise FrozenBundleError(
                f"bundle v{self.__dict__.get('report_version')} is frozen"
            )
        super().__setattr__(name, value)

    def visible_hypothesis_ids(self, module: Literal["decision_brief", "deep_research"]) -> set[str]:
        """Set of hypothesis IDs rendered in the named module."""

        target = self.decision_brief if module == "decision_brief" else self.deep_research
        return set(target.hypothesis_ids_visible)


# ---------------------------------------------------------------------------
# Mutations (annotation / resolution / revision / supplement)
# ---------------------------------------------------------------------------


ResolutionAction = Literal["accept", "downgrade", "dismiss"]


class ReportAnnotation(BaseModel):
    annotation_id: str = Field(default_factory=lambda: _new_id("ann"))
    run_id: str
    report_version: int
    target_kind: Literal["hypothesis", "finding", "evidence", "section"]
    target_id: str
    body: str
    actor: ActorRef
    created_at: datetime = Field(default_factory=_utc_now)
    idempotency_key: str


class ReportResolution(BaseModel):
    resolution_id: str = Field(default_factory=lambda: _new_id("res"))
    run_id: str
    report_version: int
    target_kind: Literal["hypothesis", "finding"]
    target_id: str
    action: ResolutionAction
    reason: str
    actor: ActorRef
    created_at: datetime = Field(default_factory=_utc_now)
    idempotency_key: str


class RevisionInfo(BaseModel):
    revision_id: str = Field(default_factory=lambda: _new_id("rev"))
    run_id: str
    based_on_report_version: int
    new_report_version: int
    summary: str
    diff_evidence_ids_added: list[str] = Field(default_factory=list)
    diff_evidence_ids_removed: list[str] = Field(default_factory=list)
    actor: ActorRef
    created_at: datetime = Field(default_factory=_utc_now)
    idempotency_key: str


ResearchTaskStatus = Literal["pending", "running", "completed", "failed"]


class ResearchTask(BaseModel):
    """Supplement-evidence task.  The only mutable side-table row.

    Lifecycle: ``pending`` → ``running`` → ``completed`` (new artifact +
    bundle version produced) or ``failed`` (old versions kept, retriable).
    """

    task_id: str = Field(default_factory=lambda: _new_id("rtask"))
    run_id: str
    target_artifact_version: int
    trigger_kind: Literal[
        "human_request_supplement",
        "auto_request_supplement",
    ]
    focus_hypothesis_id: str | None = None
    query: str
    status: ResearchTaskStatus = "pending"
    failure_reason: str | None = None
    actor: ActorRef = Field(default_factory=ActorRef)
    created_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None
    idempotency_key: str


MutationKind = Literal[
    "annotation",
    "resolution",
    "revision",
    "research_task_request",
    "research_task_complete",
    "research_task_fail",
]


class MutationReceipt(BaseModel):
    """Idempotency receipt.  Replay returns the original receipt."""

    receipt_id: str = Field(default_factory=lambda: _new_id("mrec"))
    run_id: str
    mutation_kind: MutationKind
    idempotency_key: str
    payload_hash: str
    created_at: datetime = Field(default_factory=_utc_now)
    target_report_version: int | None = None
    produced_report_version: int | None = None
    research_task_id: str | None = None
