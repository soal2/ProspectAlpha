"""Tests for reporting/renderer.py: var substitution, HTML escape, components,
slot fill, non-formal banner, empty section.

Companion file ``test_template_security.py`` covers the malicious-template
attack patterns (those tests construct raw template files and prove the
loader rejects them; this file focuses on the renderer's runtime behaviour).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest
from pydantic import BaseModel, Field

from riskagent.reporting.datasets import DatasetRegistry
from riskagent.reporting.errors import ReportBuildError
from riskagent.reporting.renderer import (
    render_module,
    substitute_vars_public,
)
from riskagent.reporting.selection import derive_selection
from riskagent.reporting.templates import TemplateRepository, load_template
from riskagent.research.models import (
    ConfidenceAssessment,
    ConfidenceComponent,
    HypothesisStatus,
    SourceTier,
)


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------


class _Company(BaseModel):
    name: str
    stock_code: str
    listing_board: str = "主板"
    prospectus_date: str = "2024-03-15"


class _Run(BaseModel):
    external_cutoff_date: str
    analysis_date: str


class _Artifact(BaseModel):
    completion_status: str
    coverage_dimension_count: int


class _Bundle(BaseModel):
    review_required: bool
    review_trigger_reasons: list[str] = Field(default_factory=list)


class _Root(BaseModel):
    company: _Company
    run: _Run
    artifact: _Artifact
    bundle: _Bundle


def _root() -> _Root:
    return _Root(
        company=_Company(name="示例公司", stock_code="01234.HK"),
        run=_Run(external_cutoff_date="2024-06-30", analysis_date="2024-07-15"),
        artifact=_Artifact(completion_status="complete", coverage_dimension_count=9),
        bundle=_Bundle(review_required=False),
    )


def _clean(hyp):
    """Clear the factory's default supporting/counter IDs so R2 doesn't fire."""

    hyp.supporting_evidence_ids = []
    hyp.counter_evidence_ids = []
    return hyp


def _verified(hypothesis_factory, *, hypothesis_id="hyp_v", importance="high"):
    return _clean(
        hypothesis_factory(
            hypothesis_id=hypothesis_id,
            status=HypothesisStatus.verified,
            importance=importance,
        )
    )


# ---------------------------------------------------------------------------
# Var substitution
# ---------------------------------------------------------------------------


def test_var_substitution_basic():
    out = substitute_vars_public("公司：{{var.company.name}}", _root())
    assert out == "公司：示例公司"


def test_var_substitution_escapes_html_special_chars():
    root = _Root(
        company=_Company(name="<b>bold & co</b>", stock_code="X"),
        run=_Run(external_cutoff_date="2024-01-01", analysis_date="2024-01-01"),
        artifact=_Artifact(completion_status="complete", coverage_dimension_count=0),
        bundle=_Bundle(review_required=False),
    )
    out = substitute_vars_public("{{var.company.name}}", root)
    # quote=False keeps quotes raw but escapes < > &
    assert "&lt;b&gt;" in out
    assert "&amp;" in out


def test_var_substitution_unknown_path_raises_r3():
    with pytest.raises(ReportBuildError, match="R3"):
        substitute_vars_public("{{var.company.nonexistent}}", _root())


def test_var_substitution_rejects_dunder():
    with pytest.raises(ReportBuildError, match="R3"):
        substitute_vars_public("{{var.company.__class__}}", _root())


def test_var_substitution_rejects_non_var_root():
    """Only ``var`` is permitted; ``user`` or other roots fail at the loader
    (the regex never matches) so the renderer leaves them alone — but the
    renderer must NOT silently evaluate arbitrary roots either."""

    out = substitute_vars_public("hello {{user.name}}", _root())
    # Unmatched token stays as-is — never evaluated.
    assert "{{user.name}}" in out


def test_var_substitution_handles_bool_and_int():
    out = substitute_vars_public(
        "review_required={{var.bundle.review_required}} dims={{var.artifact.coverage_dimension_count}}",
        _root(),
    )
    assert "review_required=否" in out
    assert "dims=9" in out


# ---------------------------------------------------------------------------
# Full module render (brief core)
# ---------------------------------------------------------------------------


def test_render_brief_core(tmp_path: Path, frozen_artifact_factory, hypothesis_factory):
    # Reuse the shipping brief_core template so the renderer is exercised
    # end-to-end against the same artifact the production API will build.
    config_dir = Path(__file__).resolve().parents[2] / "configs" / "report_templates"
    repo = TemplateRepository(config_dir)
    repo.ensure_loaded()
    template = repo.get("brief_core")
    artifact = frozen_artifact_factory(
        hypotheses=[
            _verified(hypothesis_factory, hypothesis_id="hyp_v"),
        ],
    )
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection)
    report = render_module(
        template,
        root=_root(),
        registry=registry,
        module_kind="decision_brief",
    )
    assert report.module_kind == "decision_brief"
    assert "示例公司" in report.markdown
    assert len(report.sections) == 5  # brief_core has 5 sections
    # The brief must include at least one component (risk_profile_heatmap is required).
    assert any(c.kind == "risk_profile_heatmap" for c in report.components)
    # Anchors should be unique within the module.
    anchors = {c.dataset_key for c in report.components}
    assert "risk_profile_heatmap" in anchors


# ---------------------------------------------------------------------------
# Non-formal banner
# ---------------------------------------------------------------------------


def test_non_formal_section_gets_banner(tmp_path: Path, frozen_artifact_factory, hypothesis_factory):
    config_dir = Path(__file__).resolve().parents[2] / "configs" / "report_templates"
    repo = TemplateRepository(config_dir)
    repo.ensure_loaded()
    template = repo.get("deep_core")
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_c",
            status=HypothesisStatus.contested,
            importance="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection)
    report = render_module(
        template,
        root=_root(),
        registry=registry,
        module_kind="deep_research",
    )
    matrix = next(s for s in report.sections if s.section_id == "hypothesis_matrix")
    assert matrix.non_formal is True
    assert "本章节内容不构成正式结论" in matrix.markdown


# ---------------------------------------------------------------------------
# Slot fill (deep_core + biotech fragment)
# ---------------------------------------------------------------------------


def test_deep_core_fills_slot_with_fragment(
    tmp_path: Path, frozen_artifact_factory, hypothesis_factory
):
    config_dir = Path(__file__).resolve().parents[2] / "configs" / "report_templates"
    repo = TemplateRepository(config_dir)
    repo.ensure_loaded()
    template = repo.get("deep_core")
    biotech = repo.get("fragment_biotech")
    artifact = frozen_artifact_factory(
        hypotheses=[
            _verified(hypothesis_factory),
        ],
    )
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection)
    report = render_module(
        template,
        root=_root(),
        registry=registry,
        module_kind="deep_research",
        fragment=biotech,
        target_slot_id="industry_sections",
    )
    # The 6 biotech fragment sections should now appear as SectionRenders.
    bio_section_ids = {s.section_id for s in report.sections} & {
        "bio_rd_pipeline",
        "bio_regulatory",
        "bio_patents",
        "bio_licensing",
        "bio_commercialization",
        "bio_manufacturing_quality",
    }
    assert len(bio_section_ids) == 6
    # And the fallback text should NOT appear.
    assert "本插槽由行业片段填充" not in report.markdown


def test_deep_core_slot_falls_back_when_no_fragment(
    tmp_path: Path, frozen_artifact_factory, hypothesis_factory
):
    config_dir = Path(__file__).resolve().parents[2] / "configs" / "report_templates"
    repo = TemplateRepository(config_dir)
    repo.ensure_loaded()
    template = repo.get("deep_core")
    artifact = frozen_artifact_factory(
        hypotheses=[
            _verified(hypothesis_factory),
        ],
    )
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection)
    report = render_module(
        template,
        root=_root(),
        registry=registry,
        module_kind="deep_research",
        fragment=None,
        target_slot_id=None,
    )
    assert "本插槽由行业片段填充" in report.markdown


# ---------------------------------------------------------------------------
# Component degradation propagation
# ---------------------------------------------------------------------------


def test_financial_trend_degradation_propagates_to_module(
    tmp_path: Path, frozen_artifact_factory, hypothesis_factory
):
    config_dir = Path(__file__).resolve().parents[2] / "configs" / "report_templates"
    repo = TemplateRepository(config_dir)
    repo.ensure_loaded()
    template = repo.get("deep_core")
    artifact = frozen_artifact_factory(
        hypotheses=[
            _verified(hypothesis_factory),
        ],
    )
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection, run_input_payload=None)
    report = render_module(
        template,
        root=_root(),
        registry=registry,
        module_kind="deep_research",
    )
    codes = {d.code.value for d in report.degradations}
    assert "D1" in codes
    assert "D2" in codes


# ---------------------------------------------------------------------------
# Visible hypothesis IDs (lineage cross-section)
# ---------------------------------------------------------------------------


def test_visible_hypothesis_ids_collect_from_components(
    frozen_artifact_factory, hypothesis_factory
):
    config_dir = Path(__file__).resolve().parents[2] / "configs" / "report_templates"
    repo = TemplateRepository(config_dir)
    repo.ensure_loaded()
    template = repo.get("brief_core")
    artifact = frozen_artifact_factory(
        hypotheses=[
            _verified(hypothesis_factory, hypothesis_id="hyp_visible", importance="high"),
        ],
    )
    selection = derive_selection(artifact, [])
    registry = DatasetRegistry(artifact, selection)
    report = render_module(
        template,
        root=_root(),
        registry=registry,
        module_kind="decision_brief",
    )
    assert "hyp_visible" in report.hypothesis_ids_visible
