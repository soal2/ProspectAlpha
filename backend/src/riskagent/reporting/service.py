"""Reporting service: assembles a :class:`ReportBundleV2` from a frozen artifact.

The build path is **pure and deterministic** — no LLM, no Bocha, no network.
Inputs:

* a frozen :class:`ResearchArtifact` (Phase 1)
* its :class:`RedTeamVerdict` list
* lightweight run metadata (company name, run id, cutoff dates)

Outputs:

* an immutable ``ReportBundleV2`` with two rendered modules (brief + deep),
  the shared :class:`SelectionSummary`, the list of fired
  :class:`ReviewTriggerRecord`, and any persisted
  :class:`DegradationNote` the renderer emitted.

Assembly invariants (asserted here, not silently absorbed):

* ``R5``: every hypothesis visible in the brief must also be visible in the
  deep report.  The brief never introduces new facts.
* ``R6``: every verified/material_unresolved hypothesis that has
  ``counter_evidence_ids`` must point at evidence that actually exists in
  the artifact.  Counter-evidence claims that float in the void are a
  hard build failure (the formal layer cannot carry phantom citations).
* Both modules must render against the same frozen artifact version.

``completion_status == failed`` is a build rejection: the API layer maps
:class:`BundleBuildRejected` to HTTP 409.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, Field

from riskagent.research.models import (
    RedTeamVerdict,
    ResearchArtifact,
    ResearchCompletionStatus,
)

from .errors import ReportBuildError
from .industry import select_industry_fragment
from .models import (
    DegradationNote,
    IndustryProfile,
    ModuleReport,
    ReportBundleV2,
    ReviewTriggerRecord,
    TemplateRef,
)
from .renderer import render_module
from .review_triggers import evaluate_review_triggers, is_review_required
from .selection import derive_selection, snapshot_hash
from .templates import LoadedTemplate, TemplateRepository


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class BundleBuildRejected(Exception):
    """Raised when a build is rejected before rendering starts.

    The API layer maps this to HTTP 409 (the artifact exists but cannot be
    reported on — e.g. ``completion_status == failed``).
    """

    def __init__(self, reason_code: str, detail: str = ""):
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


# ---------------------------------------------------------------------------
# Build request
# ---------------------------------------------------------------------------


class _CompanyContext(BaseModel):
    name: str = ""
    stock_code: str = ""
    listing_board: str = ""
    prospectus_date: str = ""
    business_description: str = ""


class _RunContext(BaseModel):
    run_id: str
    external_cutoff_date: str = ""
    analysis_date: str = ""


class _ArtifactContext(BaseModel):
    artifact_id: str
    version: int
    completion_status: str
    coverage_dimension_count: int = 0


class _BundleContext(BaseModel):
    review_required: bool
    review_trigger_reasons: list[str] = Field(default_factory=list)


class _RootContext(BaseModel):
    """Top-level object the renderer walks for ``{{var.path}}`` substitution."""

    company: _CompanyContext
    run: _RunContext
    artifact: _ArtifactContext
    bundle: _BundleContext


class BuildRequest(BaseModel):
    """Inputs for :meth:`ReportingService.build_bundle`.

    ``run_input_payload`` is the optional ``AnalysisRun.input_payload`` dict;
    the dataset layer pulls ``structured_data`` out of it for the
    financial-trend and peer-comparison components.
    """

    run_id: str
    artifact: ResearchArtifact
    verdicts: list[RedTeamVerdict] = Field(default_factory=list)
    company_name: str = ""
    business_description: str = ""
    stock_code: str = ""
    listing_board: str = ""
    prospectus_date: str = ""
    external_cutoff_date: str = ""
    analysis_date: str = ""
    run_input_payload: dict[str, Any] | None = None
    industry_config: Mapping[str, object] | None = None
    review_trigger_config: Mapping[str, object] | None = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ReportingService:
    """Pure-Python bundle builder.

    The service is constructed once per process and is safe to share.
    Templates are loaded lazily on first use via the repository.
    """

    BRIEF_TEMPLATE_ID = "brief_core"
    DEEP_TEMPLATE_ID = "deep_core"

    def __init__(
        self,
        template_repo: TemplateRepository,
        *,
        brief_template_id: str = BRIEF_TEMPLATE_ID,
        deep_template_id: str = DEEP_TEMPLATE_ID,
    ):
        self._repo = template_repo
        self._brief_id = brief_template_id
        self._deep_id = deep_template_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_bundle(self, request: BuildRequest) -> ReportBundleV2:
        """Render a frozen artifact into an immutable :class:`ReportBundleV2`."""

        self._reject_failed_artifact(request.artifact)
        if not request.artifact.frozen:
            raise ReportBuildError(
                "R7_FROZEN_ARTIFACT_REQUIRED: artifact must be frozen before reporting"
            )

        self._repo.ensure_loaded()
        brief_template = self._repo.get(self._brief_id)
        deep_template = self._repo.get(self._deep_id)

        selection = derive_selection(request.artifact, request.verdicts)

        # Pre-render assertions (rendering resolves evidence/hypothesis ids;
        # fail-fast on a broken artifact before the renderer hits R2).
        self._assert_counter_evidence_paired(request.artifact, selection)

        industry_profile, fragment = select_industry_fragment(
            request.artifact,
            self._repo,
            company_name=request.company_name,
            business_description=request.business_description,
            industry_config=request.industry_config,
        )

        triggers = evaluate_review_triggers(
            request.artifact,
            selection,
            request.verdicts,
            config=request.review_trigger_config,
        )
        review_required = is_review_required(triggers)

        from .datasets import DatasetRegistry  # local import to avoid cycle

        registry = DatasetRegistry(
            request.artifact,
            selection,
            run_input_payload=request.run_input_payload,
        )

        root = self._build_root(
            request,
            selection,
            pending_triggers=[t.trigger_name for t in triggers],
        )
        brief_module = render_module(
            brief_template,
            root=root,
            registry=registry,
            module_kind="decision_brief",
        )
        deep_module = render_module(
            deep_template,
            root=root,
            registry=registry,
            module_kind="deep_research",
            fragment=fragment,
            target_slot_id="industry_sections" if fragment is not None else None,
        )

        # R5: brief must be a strict subset of the deep report.
        brief_only = set(brief_module.hypothesis_ids_visible) - set(
            deep_module.hypothesis_ids_visible
        )
        if brief_only:
            raise ReportBuildError(
                "R5_BRIEF_NOT_SUBSET_OF_DEEP: "
                f"brief contains ids absent from deep: {sorted(brief_only)}"
            )

        degradations = _dedupe_degradations(
            brief_module.degradations + deep_module.degradations
        )

        bundle = ReportBundleV2(
            run_id=request.run_id,
            report_version=1,
            research_artifact_id=request.artifact.artifact_id,
            research_artifact_version=request.artifact.version,
            verdict_snapshot_hash=snapshot_hash(request.artifact, request.verdicts),
            brief_template=brief_template.ref(),
            deep_template=deep_template.ref(),
            industry_profile=_to_profile_record(industry_profile),
            decision_brief=brief_module,
            deep_research=deep_module,
            selection=selection,
            review_state="review_required" if review_required else "auto_completed",
            review_triggers=triggers,
            degradations=degradations,
        )
        bundle.frozen = True
        return bundle

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _reject_failed_artifact(artifact: ResearchArtifact) -> None:
        if artifact.completion_status is ResearchCompletionStatus.failed:
            raise BundleBuildRejected(
                "artifact_failed",
                "artifact.completion_status is 'failed'; cannot build report",
            )

    @staticmethod
    def _build_root(
        request: BuildRequest,
        selection: Any,
        *,
        pending_triggers: list[str],
    ) -> _RootContext:
        company = _CompanyContext(
            name=request.company_name,
            stock_code=request.stock_code,
            listing_board=request.listing_board,
            prospectus_date=request.prospectus_date,
            business_description=request.business_description,
        )
        run = _RunContext(
            run_id=request.run_id,
            external_cutoff_date=request.external_cutoff_date,
            analysis_date=request.analysis_date,
        )
        artifact_ctx = _ArtifactContext(
            artifact_id=request.artifact.artifact_id,
            version=request.artifact.version,
            completion_status=request.artifact.completion_status.value,
            coverage_dimension_count=len(request.artifact.plan),
        )
        bundle_ctx = _BundleContext(
            review_required=bool(pending_triggers),
            review_trigger_reasons=list(pending_triggers),
        )
        return _RootContext(
            company=company,
            run=run,
            artifact=artifact_ctx,
            bundle=bundle_ctx,
        )

    @staticmethod
    def _assert_counter_evidence_paired(
        artifact: ResearchArtifact,
        selection: Any,
    ) -> None:
        """Every counter_evidence_id on a formal hypothesis must exist in the
        artifact's evidence list."""

        evidence_ids = {card.evidence_id for card in artifact.evidence}
        formal_ids = set(selection.formal_hypothesis_ids)
        for hyp in artifact.hypotheses:
            if hyp.hypothesis_id not in formal_ids:
                continue
            for cid in hyp.counter_evidence_ids:
                if cid not in evidence_ids:
                    raise ReportBuildError(
                        "R6_COUNTER_EVIDENCE_UNPAIRED: "
                        f"hypothesis {hyp.hypothesis_id!r} cites missing counter evidence "
                        f"{cid!r}"
                    )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _to_profile_record(profile: IndustryProfile) -> IndustryProfile:
    """The industry module returns an :class:`IndustryProfile` already shaped
    for the bundle — this function exists as the single place to swap that
    in the future if the renderer needs a different ref shape."""

    return profile


def _dedupe_degradations(notes: list[DegradationNote]) -> list[DegradationNote]:
    """De-dupe degradation notes that appear in both modules.

    Notes are keyed by ``(code, section_id, detail)`` — a single underlying
    cause (e.g. ``D1`` financial-trend unavailable) shows up once per bundle,
    not once per module.
    """

    seen: set[tuple[str, str | None, str | None]] = set()
    deduped: list[DegradationNote] = []
    for note in notes:
        key = (note.code.value, note.section_id, note.detail)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(note)
    return deduped


def make_service(template_dir: Path | str | None = None) -> ReportingService:
    """Build a service backed by the canonical report-templates directory.

    ``template_dir`` defaults to ``backend/configs/report_templates`` (relative
    to the project root).
    """

    if template_dir is None:
        template_dir = Path(__file__).resolve().parents[3] / "configs" / "report_templates"
    repo = TemplateRepository(Path(template_dir))
    return ReportingService(repo)
