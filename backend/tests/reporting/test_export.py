"""Tests for reporting/export.py: Markdown export of bundles + components."""

from __future__ import annotations

from pathlib import Path

import pytest

from riskagent.reporting.export import (
    export_bundle_to_markdown,
    render_component_payload,
)
from riskagent.reporting.service import BuildRequest, make_service
from riskagent.research.models import HypothesisStatus


_CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "report_templates"


def _clean(hyp):
    hyp.supporting_evidence_ids = []
    hyp.counter_evidence_ids = []
    return hyp


def _build_bundle(artifact, **kwargs):
    service = make_service(_CONFIG_DIR)
    request = BuildRequest(
        run_id="run_export",
        artifact=artifact,
        company_name="示例公司",
        stock_code="01234.HK",
        external_cutoff_date="2024-06-30",
        analysis_date="2024-07-15",
        **kwargs,
    )
    return service.build_bundle(request)


# ---------------------------------------------------------------------------
# Bundle export end-to-end
# ---------------------------------------------------------------------------


def test_export_bundle_produces_self_contained_markdown(
    frozen_artifact_factory, hypothesis_factory
):
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(artifact)
    md = export_bundle_to_markdown(bundle)

    # Identity / meta block
    assert "run_export" in md
    assert "report_version" in md
    assert "verdict_snapshot_hash" in md
    # Both modules present
    assert "## 风险研究简报" in md
    assert "## 专项深度研究报告" in md
    # Audit appendix
    assert "## 审计附录" in md
    assert "事实层选择摘要" in md


def test_export_marks_non_formal_sections_with_banner(
    frozen_artifact_factory, hypothesis_factory
):
    """Sections flagged non_formal (e.g. hypothesis_matrix appendix) get a banner."""

    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_c",
            status=HypothesisStatus.contested,
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(artifact)
    md = export_bundle_to_markdown(bundle)
    # The deep module's hypothesis_matrix is non-formal by construction
    assert "本章节内容不构成正式结论" in md


def test_export_lists_review_triggers_when_fired(
    frozen_artifact_factory, hypothesis_factory
):
    """A bundle in review_required surfaces its trigger reasons in the audit."""

    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    hyp.title = "重大财务造假事件"
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(artifact)
    assert bundle.review_state == "review_required"
    md = export_bundle_to_markdown(bundle)
    assert "复核触发记录" in md
    assert "major_negative_allegation" in md


def test_export_notes_no_triggers_when_clean(
    frozen_artifact_factory, hypothesis_factory
):
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(artifact)
    md = export_bundle_to_markdown(bundle)
    assert "复核触发记录" in md
    assert "_无。_" in md


def test_export_lists_degradations(frozen_artifact_factory, hypothesis_factory):
    """Missing structured_data produces D1/D2 notes that surface in export."""

    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(artifact, run_input_payload=None)
    md = export_bundle_to_markdown(bundle)
    assert "降级记录" in md
    assert "D1" in md
    assert "D2" in md


# ---------------------------------------------------------------------------
# Per-component payload renderers
# ---------------------------------------------------------------------------


def test_render_risk_profile_heatmap_payload():
    payload = {
        "cells": [
            {
                "dimension": "财务",
                "severity": "high",
                "confidence_level": "high",
                "hypothesis_ids": ["hyp_v"],
            }
        ]
    }
    out = render_component_payload("risk_profile_heatmap", payload)
    assert "| 财务 | high | high | hyp_v |" in out


def test_render_risk_matrix_payload():
    payload = {
        "rows": [
            {
                "hypothesis_id": "hyp_v",
                "dimension": "财务",
                "importance": "high",
                "status": "verified",
                "confidence_level": "high",
                "supporting_count": 2,
                "counter_count": 0,
            }
        ]
    }
    out = render_component_payload("risk_matrix", payload)
    assert "hyp_v" in out
    assert "财务" in out


def test_render_risk_card_payload_with_evidence():
    payload = {
        "scope": "verified",
        "items": [
            {
                "hypothesis_id": "hyp_v",
                "title": "应收账款异常",
                "dimension": "财务",
                "importance": "high",
                "status": "verified",
                "confidence_level": "high",
                "rule_ids": ["R-01"],
                "supporting": [
                    {
                        "evidence_id": "src_a",
                        "url": "https://example.com/a",
                        "title": "来源 A",
                        "site_name": "example.com",
                        "tier": "tier1",
                        "accessible": True,
                    }
                ],
                "counter": [],
                "residual_uncertainty": "需关注季度变化",
            }
        ],
    }
    out = render_component_payload("risk_card", payload)
    assert "应收账款异常" in out
    assert "https://example.com/a" in out
    assert "来源 A" in out
    assert "tier1" in out
    assert "残余不确定性" in out


def test_render_financial_trend_unavailable_payload():
    payload = {"available": False}
    out = render_component_payload("financial_trend", payload)
    assert "不可用" in out


def test_render_financial_trend_with_points():
    payload = {
        "available": True,
        "points": [
            {"period": "2023", "metric": "毛利率", "value": 0.42, "source_evidence_id": "src_a"},
            {"period": "2022", "metric": "毛利率", "value": None, "source_evidence_id": None},
        ],
    }
    out = render_component_payload("financial_trend", payload)
    assert "2023" in out
    assert "毛利率" in out
    assert "0.42" in out
    assert "—" in out


def test_render_peer_comparison_unavailable_payload():
    payload = {"available": False}
    out = render_component_payload("peer_comparison", payload)
    assert "不可用" in out


def test_render_pipeline_chart_payload():
    payload = {
        "stages": [
            {"name": "临床 I 期", "detail": "进行中"},
            {"name": "临床 II 期", "detail": "未启动"},
        ]
    }
    out = render_component_payload("pipeline_chart", payload)
    assert "临床 I 期" in out
    assert "临床 II 期" in out


def test_render_event_timeline_marks_post_prospectus():
    payload = {
        "items": [
            {"event_date": "2024-05-01", "title": "更新 A", "evidence_id": "src_a", "is_post_prospectus_update": True},
            {"event_date": None, "title": "未注明日期", "evidence_id": "src_b", "is_post_prospectus_update": False},
        ]
    }
    out = render_component_payload("event_timeline", payload)
    assert "招股书后更新" in out
    assert "未注明" in out


def test_render_risk_causal_graph_payload():
    payload = {
        "edges": [
            {
                "from_hypothesis_id": "hyp_a",
                "to_hypothesis_id": "hyp_b",
                "shared_evidence_ids": ["src_shared"],
            }
        ]
    }
    out = render_component_payload("risk_causal_graph", payload)
    assert "hyp_a" in out
    assert "hyp_b" in out
    assert "src_shared" in out


def test_render_hypothesis_matrix_payload_has_non_formal_banner():
    payload = {
        "rows": [
            {
                "hypothesis_id": "hyp_v",
                "title": "测试假设",
                "dimension": "财务",
                "importance": "high",
                "status": "verified",
                "confidence_level": "high",
                "gate_passed": True,
                "refutation_search_done": True,
                "rule_ids": ["R-01"],
            }
        ]
    }
    out = render_component_payload("hypothesis_matrix", payload)
    assert "本章节内容不构成正式结论" in out
    assert "通过" in out  # gate passed
    assert "R-01" in out


def test_render_source_quality_payload():
    payload = {
        "summary": {
            "tier1_count": 3,
            "tier2_count": 2,
            "tier3_count": 1,
            "unknown_count": 0,
            "selected_total": 6,
            "inaccessible_count": 1,
        }
    }
    out = render_component_payload("source_quality", payload)
    assert "3" in out
    assert "tier1" in out
    assert "不可访问" in out


def test_render_research_coverage_payload():
    payload = {
        "completion_status": "complete",
        "coverage_dimensions": [
            {"dimension": "财务", "status": "done", "hypothesis_count": 3, "verified_count": 2},
        ],
        "total_hypotheses": 3,
    }
    out = render_component_payload("research_coverage", payload)
    assert "财务" in out
    assert "done" in out


def test_render_unknown_kind_degrades_to_placeholder():
    out = render_component_payload("nonexistent_kind", {})
    assert "暂无导出渲染" in out


def test_render_payload_handles_missing_fields():
    """Missing required fields degrade to a placeholder, never crash."""

    # Malformed financial_trend payload missing 'available'
    out = render_component_payload("financial_trend", {"junk": True})
    assert "不可用" in out  # available defaults to falsy → fallback path


# ---------------------------------------------------------------------------
# Empty-section handling
# ---------------------------------------------------------------------------


def test_export_handles_empty_section_markdown(
    frozen_artifact_factory, hypothesis_factory
):
    """The export never crashes when a section has no content."""

    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(artifact)
    # Mutate a section to have empty markdown to exercise the fallback
    bundle.decision_brief.sections[0].markdown = ""
    md = export_bundle_to_markdown(bundle)
    assert "_本轮未形成有效结论。_" in md
