"""Tests for reporting/datasets.py: every component kind, hard failures, lineage."""

from __future__ import annotations

import pytest

from riskagent.reporting.datasets import (
    DatasetRegistry,
    EventTimeline,
    FinancialTrend,
    HypothesisMatrix,
    PeerComparison,
    PipelineChart,
    RiskCard,
    RiskCausalGraph,
    RiskMatrix,
    RiskProfileHeatmap,
    SourceQuality,
    supported_component_kinds,
)
from riskagent.reporting.errors import ReportBuildError
from riskagent.reporting.selection import derive_selection
from riskagent.research.models import (
    ConfidenceAssessment,
    ConfidenceComponent,
    GateResult,
    HypothesisStatus,
    RedTeamDecision,
    SourceTier,
)


def _clean(hyp):
    """Strip the factory's default supporting/counter ids — tests that need
    lineage will set their own IDs and add the corresponding evidence."""

    hyp.supporting_evidence_ids = []
    hyp.counter_evidence_ids = []
    return hyp


# ---------------------------------------------------------------------------
# Helpers (factories come from conftest.py)
# ---------------------------------------------------------------------------


def _registry(frozen_artifact_factory, hypothesis_factory, verdicts=None, *, run_input_payload=None):
    artifact = frozen_artifact_factory(
        hypotheses=[
            _clean(
                hypothesis_factory(
                    hypothesis_id="hyp_v",
                    status=HypothesisStatus.verified,
                    importance="high",
                    rule_ids=("RR-001",),
                    confidence_level="high",
                )
            ),
            _clean(
                hypothesis_factory(
                    hypothesis_id="hyp_c",
                    status=HypothesisStatus.contested,
                    importance="medium",
                    confidence_level="medium",
                )
            ),
            _clean(
                hypothesis_factory(
                    hypothesis_id="hyp_p",
                    status=HypothesisStatus.partially_supported,
                    importance="low",
                )
            ),
        ],
    )
    selection = derive_selection(artifact, verdicts or [])
    return DatasetRegistry(
        artifact,
        selection,
        run_input_payload=run_input_payload,
    ), artifact, selection


# ---------------------------------------------------------------------------
# Registry + supported kinds
# ---------------------------------------------------------------------------


def test_supported_kinds_match_whitelist():
    kinds = supported_component_kinds()
    assert kinds == frozenset(
        {
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
        }
    )


def test_registry_requires_frozen_artifact():
    """Mirrors derive_selection guard: unfrozen artifact = programming error."""

    from riskagent.research.models import ResearchArtifact

    artifact = ResearchArtifact(hypotheses=[])
    with pytest.raises(ValueError, match="frozen"):
        DatasetRegistry(artifact, selection=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# risk_profile_heatmap
# ---------------------------------------------------------------------------


def test_heatmap_covers_all_baseline_dimensions(frozen_artifact_factory, hypothesis_factory):
    registry, _, _ = _registry(frozen_artifact_factory, hypothesis_factory)
    heatmap = registry.resolve("risk_profile_heatmap")
    assert isinstance(heatmap, RiskProfileHeatmap)
    assert len(heatmap.cells) == 9  # BASELINE_DIMENSIONS has 9
    by_dim = {c.dimension: c for c in heatmap.cells}
    assert by_dim["财务"].verified_count == 1
    assert by_dim["财务"].material_unresolved_count == 0
    assert by_dim["财务"].highest_importance == "high"


def test_heatmap_reflects_material_unresolved(
    frozen_artifact_factory, hypothesis_factory, verdict_factory
):
    artifact = frozen_artifact_factory(
        hypotheses=[
            _clean(
                hypothesis_factory(
                    hypothesis_id="hyp_m",
                    status=HypothesisStatus.contested,
                    importance="high",
                )
            ),
        ],
    )
    verdict = verdict_factory(
        hypothesis_id="hyp_m", decision=RedTeamDecision.material_unresolved
    )
    selection = derive_selection(artifact, [verdict])
    registry = DatasetRegistry(artifact, selection)
    heatmap = registry.resolve("risk_profile_heatmap")
    cell = next(c for c in heatmap.cells if c.dimension == "财务")
    assert cell.material_unresolved_count == 1
    assert cell.verified_count == 0


# ---------------------------------------------------------------------------
# risk_matrix
# ---------------------------------------------------------------------------


def test_risk_matrix_one_row_per_hypothesis(frozen_artifact_factory, hypothesis_factory):
    registry, _, _ = _registry(frozen_artifact_factory, hypothesis_factory)
    matrix = registry.resolve("risk_matrix")
    assert isinstance(matrix, RiskMatrix)
    assert len(matrix.rows) == 3  # hyp_v, hyp_c, hyp_p
    statuses = {r.status for r in matrix.rows}
    assert statuses == {"verified", "contested", "partially_supported"}


# ---------------------------------------------------------------------------
# risk_card (selection scopes + dimension filter + lineage)
# ---------------------------------------------------------------------------


def test_risk_card_verified_only(frozen_artifact_factory, hypothesis_factory):
    registry, _, _ = _registry(frozen_artifact_factory, hypothesis_factory)
    card = registry.resolve("risk_card", {"selection": "verified"})
    assert isinstance(card, RiskCard)
    assert card.scope == "verified"
    assert len(card.items) == 1
    assert card.items[0].hypothesis_id == "hyp_v"


def test_risk_card_material_unresolved(
    frozen_artifact_factory, hypothesis_factory, verdict_factory
):
    artifact = frozen_artifact_factory(
        hypotheses=[
            _clean(
                hypothesis_factory(
                    hypothesis_id="hyp_m",
                    status=HypothesisStatus.contested,
                    importance="high",
                )
            ),
        ],
    )
    selection = derive_selection(
        artifact,
        [verdict_factory(hypothesis_id="hyp_m", decision=RedTeamDecision.material_unresolved)],
    )
    registry = DatasetRegistry(artifact, selection)
    card = registry.resolve("risk_card", {"selection": "material_unresolved"})
    assert len(card.items) == 1
    assert card.items[0].hypothesis_id == "hyp_m"


def test_risk_card_formal_includes_verified_and_material_unresolved(
    frozen_artifact_factory, hypothesis_factory, verdict_factory
):
    artifact = frozen_artifact_factory(
        hypotheses=[
            _clean(
                hypothesis_factory(
                    hypothesis_id="hyp_v",
                    status=HypothesisStatus.verified,
                    importance="high",
                )
            ),
            _clean(
                hypothesis_factory(
                    hypothesis_id="hyp_m",
                    status=HypothesisStatus.contested,
                    importance="high",
                )
            ),
            _clean(
                hypothesis_factory(
                    hypothesis_id="hyp_x",
                    status=HypothesisStatus.partially_supported,
                )
            ),
        ],
    )
    selection = derive_selection(
        artifact,
        [verdict_factory(hypothesis_id="hyp_m", decision=RedTeamDecision.material_unresolved)],
    )
    registry = DatasetRegistry(artifact, selection)
    card = registry.resolve("risk_card", {"selection": "formal"})
    ids = {item.hypothesis_id for item in card.items}
    assert ids == {"hyp_v", "hyp_m"}
    assert "hyp_x" not in ids  # partially_supported is NOT formal


def test_risk_card_invalid_scope_raises_r6(frozen_artifact_factory, hypothesis_factory):
    registry, _, _ = _registry(frozen_artifact_factory, hypothesis_factory)
    with pytest.raises(ReportBuildError, match="R6"):
        registry.resolve("risk_card", {"selection": "weird_scope"})


def test_risk_card_carries_evidence_lineage(
    frozen_artifact_factory, hypothesis_factory, evidence_factory
):
    evidence = evidence_factory(evidence_id="src_a", tier=SourceTier.tier1)
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_l",
            status=HypothesisStatus.verified,
        )
    )
    hyp.supporting_evidence_ids = ["src_a"]
    artifact = frozen_artifact_factory(hypotheses=[hyp], evidence=[evidence])
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection)
    card = registry.resolve("risk_card", {"selection": "verified"})
    assert card.items[0].supporting[0].evidence_id == "src_a"
    assert card.items[0].supporting[0].tier == "tier1"
    assert card.items[0].supporting[0].url.endswith("src_a.html")


def test_risk_card_unknown_evidence_id_raises_r2(
    frozen_artifact_factory, hypothesis_factory
):
    hyp = _clean(hypothesis_factory(hypothesis_id="hyp_bad", status=HypothesisStatus.verified))
    hyp.supporting_evidence_ids = ["src_does_not_exist"]
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection)
    with pytest.raises(ReportBuildError, match="R2_UNKNOWN_EVIDENCE"):
        registry.resolve("risk_card", {"selection": "verified"})


# ---------------------------------------------------------------------------
# financial_trend + peer_comparison (structured data)
# ---------------------------------------------------------------------------


def test_financial_trend_missing_degrades(frozen_artifact_factory, hypothesis_factory):
    registry, _, _ = _registry(frozen_artifact_factory, hypothesis_factory)
    ds = registry.resolve("financial_trend")
    assert isinstance(ds, FinancialTrend)
    assert ds.available is False
    assert ds.degradation == "D1"
    assert ds.points == []


def test_financial_trend_present(frozen_artifact_factory, hypothesis_factory):
    payload = {
        "structured_data": {
            "financial_trend": [
                {"period": "2023", "metric": "营收", "value": 100.0, "source_evidence_id": "src_a"},
            ],
        },
    }
    registry, _, _ = _registry(
        frozen_artifact_factory, hypothesis_factory, run_input_payload=payload
    )
    ds = registry.resolve("financial_trend")
    assert ds.available is True
    assert len(ds.points) == 1
    assert ds.points[0].metric == "营收"


def test_peer_comparison_missing_degrades(frozen_artifact_factory, hypothesis_factory):
    registry, _, _ = _registry(frozen_artifact_factory, hypothesis_factory)
    ds = registry.resolve("peer_comparison")
    assert isinstance(ds, PeerComparison)
    assert ds.available is False
    assert ds.degradation == "D2"


# ---------------------------------------------------------------------------
# pipeline_chart
# ---------------------------------------------------------------------------


def test_pipeline_chart_biotech_collects_business_hypotheses(
    frozen_artifact_factory, hypothesis_factory
):
    hyp_biz = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_biz",
            status=HypothesisStatus.verified,
            dimension="业务",
        )
    )
    hyp_other = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_other",
            status=HypothesisStatus.verified,
            dimension="财务",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp_biz, hyp_other])
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection)
    chart = registry.resolve("pipeline_chart", {"industry": "biotech"})
    assert isinstance(chart, PipelineChart)
    assert len(chart.stages) == 1
    assert chart.stages[0]["hypothesis_id"] == "hyp_biz"


def test_pipeline_chart_non_biotech_returns_empty(
    frozen_artifact_factory, hypothesis_factory
):
    registry, _, _ = _registry(frozen_artifact_factory, hypothesis_factory)
    chart = registry.resolve("pipeline_chart", {"industry": "general"})
    assert chart.stages == []


# ---------------------------------------------------------------------------
# event_timeline
# ---------------------------------------------------------------------------


def test_timeline_includes_selected_evidence_only(
    frozen_artifact_factory, hypothesis_factory, evidence_factory
):
    selected = evidence_factory(evidence_id="src_t", tier=SourceTier.tier1, selection="selected")
    excluded = evidence_factory(evidence_id="src_x", tier=SourceTier.tier1, selection="excluded")
    artifact = frozen_artifact_factory(evidence=[selected, excluded])
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection)
    timeline = registry.resolve("event_timeline")
    assert isinstance(timeline, EventTimeline)
    ids = {item.evidence_id for item in timeline.items}
    assert ids == {"src_t"}


# ---------------------------------------------------------------------------
# risk_causal_graph (shared evidence → edges)
# ---------------------------------------------------------------------------


def test_causal_graph_links_hypotheses_sharing_evidence(
    frozen_artifact_factory, hypothesis_factory, evidence_factory
):
    shared = evidence_factory(evidence_id="src_shared", tier=SourceTier.tier1)
    h1 = _clean(hypothesis_factory(hypothesis_id="hyp_a", status=HypothesisStatus.verified))
    h2 = _clean(hypothesis_factory(hypothesis_id="hyp_b", status=HypothesisStatus.verified))
    h1.supporting_evidence_ids = ["src_shared"]
    h2.supporting_evidence_ids = ["src_shared"]
    artifact = frozen_artifact_factory(hypotheses=[h1, h2], evidence=[shared])
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection)
    graph = registry.resolve("risk_causal_graph")
    assert isinstance(graph, RiskCausalGraph)
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    assert graph.edges[0].shared_evidence_ids == ["src_shared"]


def test_causal_graph_tier_filter(frozen_artifact_factory, hypothesis_factory, evidence_factory):
    """``evidence_tier: tier3`` is used in weak-sources appendix to scope the graph."""

    weak = evidence_factory(evidence_id="src_w", tier=SourceTier.tier3)
    strong = evidence_factory(evidence_id="src_s", tier=SourceTier.tier1)
    h1 = _clean(hypothesis_factory(hypothesis_id="hyp_a", status=HypothesisStatus.contested))
    h2 = _clean(hypothesis_factory(hypothesis_id="hyp_b", status=HypothesisStatus.contested))
    h1.supporting_evidence_ids = ["src_w", "src_s"]
    h2.supporting_evidence_ids = ["src_w", "src_s"]
    artifact = frozen_artifact_factory(hypotheses=[h1, h2], evidence=[weak, strong])
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection)
    graph = registry.resolve("risk_causal_graph", {"evidence_tier": "tier3"})
    # Only the weak evidence contributes an edge; the tier1 source is filtered out.
    assert len(graph.edges) == 1
    assert graph.edges[0].shared_evidence_ids == ["src_w"]


# ---------------------------------------------------------------------------
# hypothesis_matrix (non-formal appendix)
# ---------------------------------------------------------------------------


def test_hypothesis_matrix_lists_non_formal_statuses(
    frozen_artifact_factory, hypothesis_factory
):
    registry, _, _ = _registry(frozen_artifact_factory, hypothesis_factory)
    matrix = registry.resolve("hypothesis_matrix")
    assert isinstance(matrix, HypothesisMatrix)
    statuses = {row.status for row in matrix.rows}
    # Default include_statuses: partially_supported, contested, inconclusive, refuted.
    assert statuses == {"partially_supported", "contested"}
    for row in matrix.rows:
        assert row.non_formal is True
    assert matrix.non_formal_banner.startswith("本章节内容不构成正式结论")


def test_hypothesis_matrix_respects_explicit_include_statuses(
    frozen_artifact_factory, hypothesis_factory
):
    registry, _, _ = _registry(frozen_artifact_factory, hypothesis_factory)
    matrix = registry.resolve(
        "hypothesis_matrix",
        {"include_statuses": ["contested"]},
    )
    statuses = {row.status for row in matrix.rows}
    assert statuses == {"contested"}


# ---------------------------------------------------------------------------
# source_quality
# ---------------------------------------------------------------------------


def test_source_quality_groups_by_tier(frozen_artifact_factory, evidence_factory):
    evidence = [
        evidence_factory(evidence_id="t1", tier=SourceTier.tier1, selection="selected"),
        evidence_factory(evidence_id="t2a", tier=SourceTier.tier2, selection="selected"),
        evidence_factory(evidence_id="t2b", tier=SourceTier.tier2, selection="selected"),
        evidence_factory(evidence_id="t3", tier=SourceTier.tier3, selection="selected"),
        evidence_factory(evidence_id="uk", tier=SourceTier.unknown, selection="selected"),
        evidence_factory(evidence_id="excluded", tier=SourceTier.tier1, selection="excluded"),
    ]
    artifact = frozen_artifact_factory(evidence=evidence)
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection)
    ds = registry.resolve("source_quality")
    assert isinstance(ds, SourceQuality)
    assert ds.summary.tier1_count == 1
    assert ds.summary.tier2_count == 2
    assert ds.summary.tier3_count == 1
    assert ds.summary.unknown_count == 1
    assert ds.summary.selected_total == 5
    assert {ref.evidence_id for ref in ds.by_tier["tier2"]} == {"t2a", "t2b"}


def test_source_quality_summary_only_omits_by_tier(
    frozen_artifact_factory, evidence_factory
):
    artifact = frozen_artifact_factory(
        evidence=[evidence_factory(evidence_id="t1", tier=SourceTier.tier1)]
    )
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection)
    ds = registry.resolve("source_quality", {"summary_only": True})
    assert ds.summary.tier1_count == 1
    assert ds.by_tier == {}


# ---------------------------------------------------------------------------
# research_coverage
# ---------------------------------------------------------------------------


def test_research_coverage_lists_planned_dimensions(
    frozen_artifact_factory, hypothesis_factory
):
    # The factory default builds one coverage task for 财务 only; add more via plan_dimensions.
    artifact = frozen_artifact_factory(
        hypotheses=[
            _clean(
                hypothesis_factory(hypothesis_id="hyp_a", status=HypothesisStatus.verified)
            ),
        ],
        plan_dimensions=["财务", "治理", "业务"],
    )
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection)
    coverage = registry.resolve("research_coverage")
    dims = {d.dimension for d in coverage.coverage_dimensions}
    assert dims == {"财务", "治理", "业务"}
    assert coverage.completion_status == "complete"
    assert coverage.total_hypotheses == 1


# ---------------------------------------------------------------------------
# Unknown kind hard-failure (R1)
# ---------------------------------------------------------------------------


def test_unknown_kind_raises_r1(frozen_artifact_factory, hypothesis_factory):
    registry, _, _ = _registry(frozen_artifact_factory, hypothesis_factory)
    with pytest.raises(ReportBuildError, match="R1_UNKNOWN_COMPONENT_KIND"):
        registry.resolve("totally_unknown_kind")
