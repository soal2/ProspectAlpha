r"""Dataset registry: derive component data from a frozen artifact + verdicts.

Components are the 11 whitelisted kinds the template DSL accepts inside
``\`\`\`component`` blocks (see templates.py).  Each kind maps to a
deterministic, fully-derived JSON-serialisable payload built from the
frozen artifact and its red-team verdicts.  The renderer (Step 5) calls
into this module to resolve each component before substitution.

Hard rules (R-series failures, surfaced as ``ReportBuildError``):

* R1 — unknown component kind (caller bug, never user-facing)
* R2 — referenced hypothesis/evidence id does not exist in this artifact
* R3 — component params fail per-kind validation
* R4 — financial_trend / peer_comparison request structured data that
       isn't on the run input payload → degrade in the caller (returns
       empty dataset + the caller records D1)
* R5 — cross-artifact reference (component params mention an id from a
       different artifact version)
* R6 — selection scope invalid (e.g. ``formal`` requested in a kind
       that doesn't support it)
* R7 — material_unresolved requested in a kind that only accepts verified

The dataset for every kind is a Pydantic model so callers can rely on
field names; the registry is intentionally small and exhaustive.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, Field

from riskagent.research.models import (
    ConfidenceAssessment,
    GateResult,
    HypothesisStatus,
    RedTeamVerdict,
    ResearchArtifact,
    RiskHypothesis,
    SourceCard,
    SourceTier,
)

from .errors import DegradationCode, ReportBuildError
from .models import SelectionSummary

# re-exported so callers can introspect
__all__ = [
    "DatasetRegistry",
    "supported_component_kinds",
]


# ---------------------------------------------------------------------------
# Per-kind dataset models (lineage preserved)
# ---------------------------------------------------------------------------


class _EvidenceRef(BaseModel):
    """Compact evidence reference with enough lineage for the UI to deep-link."""

    evidence_id: str
    url: str
    title: str | None = None
    site_name: str | None = None
    tier: str
    accessible: bool = True


class _HypothesisRef(BaseModel):
    hypothesis_id: str
    title: str
    dimension: str
    importance: str
    status: str
    confidence_level: str | None = None
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    counter_evidence_ids: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)


class RiskProfileHeatmapCell(BaseModel):
    dimension: str
    highest_importance: str | None  # "high" | "medium" | "low" | None
    verified_count: int
    material_unresolved_count: int
    contested_count: int


class RiskProfileHeatmap(BaseModel):
    kind: str = "risk_profile_heatmap"
    cells: list[RiskProfileHeatmapCell]
    source_hypothesis_ids: list[str]


class RiskMatrixRow(BaseModel):
    dimension: str
    importance: str
    confidence_level: str | None
    status: str
    supporting_count: int
    counter_count: int


class RiskMatrix(BaseModel):
    kind: str = "risk_matrix"
    rows: list[RiskMatrixRow]
    source_hypothesis_ids: list[str]


class RiskCardItem(BaseModel):
    hypothesis_id: str
    title: str
    dimension: str
    importance: str
    status: str
    confidence_level: str | None
    rule_ids: list[str]
    supporting: list[_EvidenceRef]
    counter: list[_EvidenceRef]
    residual_uncertainty: str | None = None


class RiskCard(BaseModel):
    kind: str = "risk_card"
    scope: str  # "verified" | "material_unresolved" | "formal" | dimension name
    items: list[RiskCardItem]


class FinancialTrendPoint(BaseModel):
    period: str
    metric: str
    value: float | None
    source_evidence_id: str | None = None


class FinancialTrend(BaseModel):
    kind: str = "financial_trend"
    available: bool
    points: list[FinancialTrendPoint] = Field(default_factory=list)
    degradation: str | None = None  # set when structured data missing


class PipelineChart(BaseModel):
    kind: str = "pipeline_chart"
    stages: list[dict[str, Any]] = Field(default_factory=list)
    source_hypothesis_ids: list[str] = Field(default_factory=list)


class PeerComparisonRow(BaseModel):
    peer: str
    metric: str
    value: float | None
    source_evidence_id: str | None = None


class PeerComparison(BaseModel):
    kind: str = "peer_comparison"
    available: bool
    rows: list[PeerComparisonRow] = Field(default_factory=list)
    degradation: str | None = None


class EventTimelineItem(BaseModel):
    event_date: str | None
    title: str
    evidence_id: str | None = None
    is_post_prospectus_update: bool = False


class EventTimeline(BaseModel):
    kind: str = "event_timeline"
    items: list[EventTimelineItem]


class RiskCausalGraphEdge(BaseModel):
    from_hypothesis_id: str
    to_hypothesis_id: str
    shared_evidence_ids: list[str]


class RiskCausalGraph(BaseModel):
    kind: str = "risk_causal_graph"
    nodes: list[_HypothesisRef]
    edges: list[RiskCausalGraphEdge]


class HypothesisMatrixRow(BaseModel):
    hypothesis_id: str
    title: str
    dimension: str
    importance: str
    status: str
    confidence_level: str | None
    gate_passed: bool | None
    refutation_search_done: bool
    rule_ids: list[str]
    non_formal: bool = True  # matrix appendix is always non-formal


class HypothesisMatrix(BaseModel):
    kind: str = "hypothesis_matrix"
    rows: list[HypothesisMatrixRow]
    non_formal_banner: str = "本章节内容不构成正式结论。"


class SourceQualitySummary(BaseModel):
    tier1_count: int
    tier2_count: int
    tier3_count: int
    unknown_count: int
    selected_total: int
    inaccessible_count: int


class SourceQuality(BaseModel):
    kind: str = "source_quality"
    summary: SourceQualitySummary
    by_tier: dict[str, list[_EvidenceRef]]


class ResearchCoverageDimension(BaseModel):
    dimension: str
    status: str
    hypothesis_count: int
    verified_count: int


class ResearchCoverage(BaseModel):
    kind: str = "research_coverage"
    completion_status: str
    coverage_dimensions: list[ResearchCoverageDimension]
    total_hypotheses: int


Dataset = (
    RiskProfileHeatmap
    | RiskMatrix
    | RiskCard
    | FinancialTrend
    | PipelineChart
    | PeerComparison
    | EventTimeline
    | RiskCausalGraph
    | HypothesisMatrix
    | SourceQuality
    | ResearchCoverage
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_known_kind(kind: str) -> None:
    if kind not in _DATASET_BUILDERS:
        raise ReportBuildError(f"R1_UNKNOWN_COMPONENT_KIND: {kind!r}")


def _hypothesis_or_raise(artifact: ResearchArtifact, hypothesis_id: str) -> RiskHypothesis:
    hyp = artifact.get_hypothesis(hypothesis_id)
    if hyp is None:
        raise ReportBuildError(f"R2_UNKNOWN_HYPOTHESIS: {hypothesis_id!r}")
    return hyp


def _evidence_or_raise(artifact: ResearchArtifact, evidence_id: str) -> SourceCard:
    card = artifact.get_evidence(evidence_id)
    if card is None:
        raise ReportBuildError(f"R2_UNKNOWN_EVIDENCE: {evidence_id!r}")
    return card


def _evidence_ref(card: SourceCard) -> _EvidenceRef:
    return _EvidenceRef(
        evidence_id=card.evidence_id,
        url=card.url,
        title=card.title,
        site_name=card.site_name,
        tier=card.tier.value,
        accessible=card.accessible,
    )


def _hypothesis_ref(hyp: RiskHypothesis) -> _HypothesisRef:
    confidence_level: str | None = (
        hyp.confidence.level if hyp.confidence is not None else None
    )
    return _HypothesisRef(
        hypothesis_id=hyp.hypothesis_id,
        title=hyp.title,
        dimension=hyp.dimension,
        importance=hyp.importance,
        status=hyp.status.value,
        confidence_level=confidence_level,
        supporting_evidence_ids=list(hyp.supporting_evidence_ids),
        counter_evidence_ids=list(hyp.counter_evidence_ids),
        rule_ids=list(hyp.rule_ids),
    )


def _confidence_level(hyp: RiskHypothesis) -> str | None:
    return hyp.confidence.level if hyp.confidence is not None else None


def _filter_hypotheses(
    artifact: ResearchArtifact,
    selection: SelectionSummary,
    *,
    scope: str,
) -> list[RiskHypothesis]:
    """Pick hypotheses by the ``scope``/``selection`` param.

    * ``verified`` — selection.verified_hypothesis_ids
    * ``material_unresolved`` — selection.material_unresolved_hypothesis_ids
    * ``formal`` — verified + material_unresolved (the formal layer)
    * ``dimension:<name>`` — all hypotheses in a dimension (any status)
    * ``all`` — all hypotheses (matrix / causal graph only)
    """

    if scope == "verified":
        ids = selection.verified_hypothesis_ids
    elif scope == "material_unresolved":
        ids = selection.material_unresolved_hypothesis_ids
    elif scope in ("formal", "all"):
        ids = (
            selection.verified_hypothesis_ids
            + selection.material_unresolved_hypothesis_ids
        )
        if scope == "all":
            ids = ids + selection.contested_hypothesis_ids
            ids = ids + selection.partially_supported_hypothesis_ids
            ids = ids + selection.refuted_hypothesis_ids
            ids = ids + selection.inconclusive_hypothesis_ids
    elif scope.startswith("dimension:"):
        dim = scope.split(":", 1)[1]
        return [h for h in artifact.hypotheses if h.dimension == dim]
    else:
        raise ReportBuildError(f"R6_INVALID_SELECTION_SCOPE: {scope!r}")
    id_set = set(ids)
    return [h for h in artifact.hypotheses if h.hypothesis_id in id_set]


# ---------------------------------------------------------------------------
# Per-kind builders
# ---------------------------------------------------------------------------


def _build_risk_profile_heatmap(
    artifact: ResearchArtifact,
    selection: SelectionSummary,
    params: Mapping[str, Any],
) -> RiskProfileHeatmap:
    dimensions: list[str] = params.get("dimensions") if isinstance(params.get("dimensions"), list) else []
    if not dimensions or dimensions == ["all"]:
        from riskagent.research.models import BASELINE_DIMENSIONS

        dimensions = list(BASELINE_DIMENSIONS)
    cells: list[RiskProfileHeatmapCell] = []
    for dim in dimensions:
        bucket = [h for h in artifact.hypotheses if h.dimension == dim]
        if not bucket:
            cells.append(
                RiskProfileHeatmapCell(
                    dimension=dim,
                    highest_importance=None,
                    verified_count=0,
                    material_unresolved_count=0,
                    contested_count=0,
                )
            )
            continue
        importance_rank = {"high": 3, "medium": 2, "low": 1}
        highest = max(
            bucket, key=lambda h: importance_rank.get(h.importance, 0)
        ).importance
        v_ids = set(selection.verified_hypothesis_ids)
        m_ids = set(selection.material_unresolved_hypothesis_ids)
        c_ids = set(selection.contested_hypothesis_ids)
        cells.append(
            RiskProfileHeatmapCell(
                dimension=dim,
                highest_importance=highest,
                verified_count=sum(1 for h in bucket if h.hypothesis_id in v_ids),
                material_unresolved_count=sum(1 for h in bucket if h.hypothesis_id in m_ids),
                contested_count=sum(1 for h in bucket if h.hypothesis_id in c_ids),
            )
        )
    return RiskProfileHeatmap(
        cells=cells,
        source_hypothesis_ids=[h.hypothesis_id for h in artifact.hypotheses],
    )


def _build_risk_matrix(
    artifact: ResearchArtifact,
    selection: SelectionSummary,
    params: Mapping[str, Any],
) -> RiskMatrix:
    rows: list[RiskMatrixRow] = []
    for hyp in artifact.hypotheses:
        rows.append(
            RiskMatrixRow(
                dimension=hyp.dimension,
                importance=hyp.importance,
                confidence_level=_confidence_level(hyp),
                status=hyp.status.value,
                supporting_count=len(hyp.supporting_evidence_ids),
                counter_count=len(hyp.counter_evidence_ids),
            )
        )
    return RiskMatrix(
        rows=rows,
        source_hypothesis_ids=[h.hypothesis_id for h in artifact.hypotheses],
    )


def _build_risk_card(
    artifact: ResearchArtifact,
    selection: SelectionSummary,
    params: Mapping[str, Any],
) -> RiskCard:
    scope = params.get("selection") or params.get("scope") or "formal"
    if scope not in {"verified", "material_unresolved", "formal"} and not scope.startswith("dimension:"):
        raise ReportBuildError(f"R6_INVALID_SELECTION_SCOPE: {scope!r}")
    if "dimension" in params and scope in {"verified", "material_unresolved", "formal"}:
        # Allow `dimension:` filter combined with a selection list.
        dim = params["dimension"]
        scope_dim = f"dimension:{dim}"
        # Intersect: formal in this dimension.
        scoped = _filter_hypotheses(artifact, selection, scope=scope_dim)
        selection_ids = {
            h.hypothesis_id for h in _filter_hypotheses(artifact, selection, scope=scope)
        }
        hypotheses = [h for h in scoped if h.hypothesis_id in selection_ids]
    else:
        hypotheses = _filter_hypotheses(artifact, selection, scope=scope)
    items: list[RiskCardItem] = []
    for hyp in hypotheses:
        supporting_cards = [
            _evidence_or_raise(artifact, eid) for eid in hyp.supporting_evidence_ids
        ]
        counter_cards = [
            _evidence_or_raise(artifact, eid) for eid in hyp.counter_evidence_ids
        ]
        items.append(
            RiskCardItem(
                hypothesis_id=hyp.hypothesis_id,
                title=hyp.title,
                dimension=hyp.dimension,
                importance=hyp.importance,
                status=hyp.status.value,
                confidence_level=_confidence_level(hyp),
                rule_ids=list(hyp.rule_ids),
                supporting=[_evidence_ref(c) for c in supporting_cards],
                counter=[_evidence_ref(c) for c in counter_cards],
                residual_uncertainty=hyp.residual_uncertainty,
            )
        )
    return RiskCard(scope=scope, items=items)


def _build_financial_trend(
    artifact: ResearchArtifact,
    selection: SelectionSummary,
    params: Mapping[str, Any],
    *,
    run_input_payload: Mapping[str, Any] | None,
) -> FinancialTrend:
    structured = (run_input_payload or {}).get("structured_data") or {}
    trends = structured.get("financial_trend")
    if not isinstance(trends, list) or not trends:
        return FinancialTrend(
            available=False,
            degradation=DegradationCode.D1_FINANCIAL_TREND_UNAVAILABLE.value,
        )
    points: list[FinancialTrendPoint] = []
    for entry in trends:
        if not isinstance(entry, Mapping):
            continue
        points.append(
            FinancialTrendPoint(
                period=str(entry.get("period", "")),
                metric=str(entry.get("metric", "")),
                value=entry.get("value"),
                source_evidence_id=entry.get("source_evidence_id"),
            )
        )
    return FinancialTrend(available=True, points=points)


def _build_pipeline_chart(
    artifact: ResearchArtifact,
    selection: SelectionSummary,
    params: Mapping[str, Any],
) -> PipelineChart:
    industry = params.get("industry")
    if industry != "biotech":
        return PipelineChart(stages=[])
    biotech_hyps = [
        h for h in artifact.hypotheses if h.dimension in {"业务", "研发"}
    ]
    stages: list[dict[str, Any]] = []
    for hyp in biotech_hyps:
        stages.append(
            {
                "title": hyp.title,
                "hypothesis_id": hyp.hypothesis_id,
                "dimension": hyp.dimension,
                "status": hyp.status.value,
            }
        )
    return PipelineChart(
        stages=stages,
        source_hypothesis_ids=[h.hypothesis_id for h in biotech_hyps],
    )


def _build_peer_comparison(
    artifact: ResearchArtifact,
    selection: SelectionSummary,
    params: Mapping[str, Any],
    *,
    run_input_payload: Mapping[str, Any] | None,
) -> PeerComparison:
    structured = (run_input_payload or {}).get("structured_data") or {}
    peers = structured.get("peer_comparison")
    if not isinstance(peers, list) or not peers:
        return PeerComparison(
            available=False,
            degradation=DegradationCode.D2_PEER_COMPARISON_UNAVAILABLE.value,
        )
    rows: list[PeerComparisonRow] = []
    for entry in peers:
        if not isinstance(entry, Mapping):
            continue
        rows.append(
            PeerComparisonRow(
                peer=str(entry.get("peer", "")),
                metric=str(entry.get("metric", "")),
                value=entry.get("value"),
                source_evidence_id=entry.get("source_evidence_id"),
            )
        )
    return PeerComparison(available=True, rows=rows)


def _build_event_timeline(
    artifact: ResearchArtifact,
    selection: SelectionSummary,
    params: Mapping[str, Any],
) -> EventTimeline:
    items: list[EventTimelineItem] = []
    for card in artifact.evidence:
        if card.selection != "selected":
            continue
        if not card.title:
            continue
        items.append(
            EventTimelineItem(
                event_date=card.event_date,
                title=card.title,
                evidence_id=card.evidence_id,
                is_post_prospectus_update=card.is_post_prospectus_update,
            )
        )
    items.sort(key=lambda i: (i.event_date or "", i.title))
    return EventTimeline(items=items)


def _build_risk_causal_graph(
    artifact: ResearchArtifact,
    selection: SelectionSummary,
    params: Mapping[str, Any],
) -> RiskCausalGraph:
    """Connect hypotheses that share supporting evidence.

    A shared evidence card means two agents anchored to the same source;
    the UI renders this as an edge to highlight implicit dependency.
    """

    tier_filter = params.get("evidence_tier")
    nodes: list[_HypothesisRef] = []
    ev_to_hyps: dict[str, list[str]] = {}
    for hyp in artifact.hypotheses:
        supporting_ids = list(hyp.supporting_evidence_ids)
        if tier_filter:
            supporting_ids = [
                eid
                for eid in supporting_ids
                if (card := artifact.get_evidence(eid))
                and card.tier.value == tier_filter
            ]
        nodes.append(_hypothesis_ref(hyp))
        for eid in supporting_ids:
            ev_to_hyps.setdefault(eid, []).append(hyp.hypothesis_id)
    edges: list[RiskCausalGraphEdge] = []
    seen: set[tuple[str, str]] = set()
    for eid, hyps in ev_to_hyps.items():
        if len(hyps) < 2:
            continue
        for i, a in enumerate(hyps):
            for b in hyps[i + 1 :]:
                key = (a, b) if a < b else (b, a)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(
                    RiskCausalGraphEdge(
                        from_hypothesis_id=key[0],
                        to_hypothesis_id=key[1],
                        shared_evidence_ids=[eid],
                    )
                )
    return RiskCausalGraph(nodes=nodes, edges=edges)


def _build_hypothesis_matrix(
    artifact: ResearchArtifact,
    selection: SelectionSummary,
    params: Mapping[str, Any],
) -> HypothesisMatrix:
    include_statuses = set(params.get("include_statuses") or [])
    if not include_statuses:
        # Default: all non-formal statuses (the matrix is the non-formal appendix).
        include_statuses = {
            HypothesisStatus.partially_supported.value,
            HypothesisStatus.contested.value,
            HypothesisStatus.inconclusive.value,
            HypothesisStatus.refuted.value,
        }
    rows: list[HypothesisMatrixRow] = []
    for hyp in artifact.hypotheses:
        if hyp.status.value not in include_statuses:
            continue
        rows.append(
            HypothesisMatrixRow(
                hypothesis_id=hyp.hypothesis_id,
                title=hyp.title,
                dimension=hyp.dimension,
                importance=hyp.importance,
                status=hyp.status.value,
                confidence_level=_confidence_level(hyp),
                gate_passed=hyp.gate.passed if hyp.gate else None,
                refutation_search_done=hyp.refutation_search_done,
                rule_ids=list(hyp.rule_ids),
            )
        )
    return HypothesisMatrix(rows=rows)


def _build_source_quality(
    artifact: ResearchArtifact,
    selection: SelectionSummary,
    params: Mapping[str, Any],
) -> SourceQuality:
    summary_only = bool(params.get("summary_only"))
    buckets: dict[str, list[_EvidenceRef]] = {
        SourceTier.tier1.value: [],
        SourceTier.tier2.value: [],
        SourceTier.tier3.value: [],
        SourceTier.unknown.value: [],
    }
    selected = [c for c in artifact.evidence if c.selection == "selected"]
    inaccessible = sum(1 for c in selected if not c.accessible)
    for card in selected:
        buckets[card.tier.value].append(_evidence_ref(card))
    summary = SourceQualitySummary(
        tier1_count=len(buckets[SourceTier.tier1.value]),
        tier2_count=len(buckets[SourceTier.tier2.value]),
        tier3_count=len(buckets[SourceTier.tier3.value]),
        unknown_count=len(buckets[SourceTier.unknown.value]),
        selected_total=len(selected),
        inaccessible_count=inaccessible,
    )
    by_tier = {} if summary_only else buckets
    return SourceQuality(summary=summary, by_tier=by_tier)


def _build_research_coverage(
    artifact: ResearchArtifact,
    selection: SelectionSummary,
    params: Mapping[str, Any],
) -> ResearchCoverage:
    plan_dimensions: list[str] = []
    for task in artifact.plan:
        if task.dimension not in plan_dimensions:
            plan_dimensions.append(task.dimension)
    dim_status: dict[str, str] = {t.dimension: t.status for t in artifact.plan}
    coverage_dimensions: list[ResearchCoverageDimension] = []
    v_ids = set(selection.verified_hypothesis_ids)
    for dim in plan_dimensions:
        bucket = [h for h in artifact.hypotheses if h.dimension == dim]
        coverage_dimensions.append(
            ResearchCoverageDimension(
                dimension=dim,
                status=dim_status.get(dim, "pending"),
                hypothesis_count=len(bucket),
                verified_count=sum(1 for h in bucket if h.hypothesis_id in v_ids),
            )
        )
    return ResearchCoverage(
        completion_status=artifact.completion_status.value,
        coverage_dimensions=coverage_dimensions,
        total_hypotheses=len(artifact.hypotheses),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_DATASET_BUILDERS: dict[str, Any] = {
    "risk_profile_heatmap": _build_risk_profile_heatmap,
    "risk_matrix": _build_risk_matrix,
    "risk_card": _build_risk_card,
    "financial_trend": _build_financial_trend,
    "pipeline_chart": _build_pipeline_chart,
    "peer_comparison": _build_peer_comparison,
    "event_timeline": _build_event_timeline,
    "risk_causal_graph": _build_risk_causal_graph,
    "hypothesis_matrix": _build_hypothesis_matrix,
    "source_quality": _build_source_quality,
    "research_coverage": _build_research_coverage,
}


class DatasetRegistry:
    """Resolve whitelisted component kinds to derived datasets.

    All builders are deterministic and operate solely on the frozen
    artifact + the selection summary (itself derived from artifact +
    verdicts).  ``run_input_payload`` is only consumed by the two
    "external structured data" components (financial_trend,
    peer_comparison); when missing, those components degrade explicitly
    rather than failing.
    """

    def __init__(
        self,
        artifact: ResearchArtifact,
        selection: SelectionSummary,
        *,
        run_input_payload: Mapping[str, Any] | None = None,
    ) -> None:
        if not artifact.frozen:
            raise ValueError("DatasetRegistry requires a frozen artifact")
        self._artifact = artifact
        self._selection = selection
        self._run_input_payload = run_input_payload

    def resolve(self, kind: str, params: Mapping[str, Any] | None = None) -> Dataset:
        _require_known_kind(kind)
        builder = _DATASET_BUILDERS[kind]
        params = params or {}
        if kind in {"financial_trend", "peer_comparison"}:
            return builder(
                self._artifact,
                self._selection,
                params,
                run_input_payload=self._run_input_payload,
            )
        return builder(self._artifact, self._selection, params)

    def supported_kinds(self) -> Sequence[str]:
        return tuple(_DATASET_BUILDERS.keys())


def supported_component_kinds() -> frozenset[str]:
    """Public mirror of the whitelisted kinds (kept separate from templates.py
    so the renderer can cross-check without importing private state)."""

    return frozenset(_DATASET_BUILDERS.keys())
