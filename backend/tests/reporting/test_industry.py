"""Tests for reporting/industry.py: biotech keyword detection + fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from riskagent.reporting.industry import (
    detect_industry,
    fallback_profile,
    select_industry_fragment,
)
from riskagent.reporting.templates import TemplateRepository
from riskagent.research.models import HypothesisStatus, SourceTier


_CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "report_templates"


def _load_yaml_config():
    from riskagent.reporting.templates import load_yaml_config

    return load_yaml_config(_CONFIG_DIR, "industry_detection.yaml")


# ---------------------------------------------------------------------------
# detect_industry: keyword matching
# ---------------------------------------------------------------------------


def test_no_signal_falls_back_to_general(frozen_artifact_factory):
    artifact = frozen_artifact_factory()
    detection = detect_industry(artifact, company_name="示例公司")
    assert detection.industry == "general"
    assert detection.matched_keywords == []
    assert "fallback" in detection.reason


def test_company_name_with_biotech_keyword(frozen_artifact_factory):
    artifact = frozen_artifact_factory()
    detection = detect_industry(artifact, company_name="某生物制药股份有限公司")
    assert detection.industry == "biotech"
    assert "生物制药" in detection.matched_keywords


def test_business_description_with_biotech_keyword(frozen_artifact_factory):
    artifact = frozen_artifact_factory()
    detection = detect_industry(
        artifact,
        company_name="示例公司",
        business_description="公司主营业务为创新药研发与临床试验",
    )
    assert detection.industry == "biotech"
    assert any(kw in {"创新药", "临床试验"} for kw in detection.matched_keywords)


def test_evidence_snippet_triggers_biotech(
    frozen_artifact_factory, evidence_factory
):
    evidence = evidence_factory(
        evidence_id="src_bio",
        tier=SourceTier.tier1,
        selection="selected",
    )
    evidence.title = "GMP认证检查结果"
    artifact = frozen_artifact_factory(evidence=[evidence])
    detection = detect_industry(artifact, company_name="某制药")
    assert detection.industry == "biotech"
    assert "GMP认证" in detection.matched_keywords


def test_hypothesis_statement_triggers_biotech(
    frozen_artifact_factory, hypothesis_factory
):
    hyp = hypothesis_factory(hypothesis_id="hyp_b", status=HypothesisStatus.verified)
    hyp.title = "在研单克隆抗体临床进展"
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    detection = detect_industry(artifact, company_name="某制药")
    assert detection.industry == "biotech"


def test_general_company_stays_general(
    frozen_artifact_factory, hypothesis_factory
):
    hyp = hypothesis_factory(hypothesis_id="hyp_v", status=HypothesisStatus.verified)
    hyp.title = "应收账款异常增长"
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    detection = detect_industry(artifact, company_name="某建筑建材集团")
    assert detection.industry == "general"


def test_default_keywords_match_when_no_config_supplied(frozen_artifact_factory):
    """The detector must work even if the YAML config is missing — Phase 2
    hard-codes the biotech keyword list as a safety net."""

    artifact = frozen_artifact_factory()
    detection = detect_industry(
        artifact,
        company_name="某生物制品公司",
        industry_config=None,
    )
    assert detection.industry == "biotech"


def test_industry_config_can_extend_keywords(frozen_artifact_factory):
    config = {
        "industries": [
            {
                "id": "biotech",
                "fragment_template_id": "fragment_biotech",
                "keywords": ["生物科技"],
            },
            {
                "id": "general",
                "fragment_template_id": "fragment_general",
                "keywords": [],
                "is_default": True,
            },
        ]
    }
    artifact = frozen_artifact_factory()
    detection = detect_industry(
        artifact,
        company_name="某生物科技股份",
        industry_config=config,
    )
    assert detection.industry == "biotech"
    assert detection.fragment_template_id == "fragment_biotech"


# ---------------------------------------------------------------------------
# select_industry_fragment: full template resolution
# ---------------------------------------------------------------------------


def test_select_biotech_fragment_against_real_repo(frozen_artifact_factory):
    repo = TemplateRepository(_CONFIG_DIR)
    repo.ensure_loaded()
    artifact = frozen_artifact_factory()
    profile, fragment = select_industry_fragment(
        artifact,
        repo,
        company_name="某生物制药公司",
        industry_config=_load_yaml_config(),
    )
    assert profile.detected == "biotech"
    assert profile.fragment is not None
    assert profile.fragment.template_id == "fragment_biotech"
    assert fragment is not None
    assert fragment.kind == "fragment"


def test_select_general_fragment_when_no_signal(frozen_artifact_factory):
    repo = TemplateRepository(_CONFIG_DIR)
    repo.ensure_loaded()
    artifact = frozen_artifact_factory()
    profile, fragment = select_industry_fragment(
        artifact,
        repo,
        company_name="某建材集团",
        industry_config=_load_yaml_config(),
    )
    assert profile.detected == "general"
    assert profile.fragment is not None
    assert profile.fragment.template_id == "fragment_general"
    assert fragment is not None


def test_fallback_profile_marks_detection_unavailable():
    profile = fallback_profile(reason="probe reason")
    assert profile.detected == "general"
    assert profile.fragment is None
    assert profile.detection_reason == "probe reason"


# ---------------------------------------------------------------------------
# Detection reason is informative for the bundle's audit trail
# ---------------------------------------------------------------------------


def test_detection_reason_lists_matched_keywords(frozen_artifact_factory):
    artifact = frozen_artifact_factory()
    detection = detect_industry(
        artifact,
        company_name="某创新药生物制药集团",
    )
    assert "创新药" in detection.reason or "生物制药" in detection.reason


def test_empty_haystack_records_reason(frozen_artifact_factory):
    """An artifact with no text signal (no evidence titles, no hypothesis
    statements, no company name) must produce a tracked fallback reason."""

    artifact = frozen_artifact_factory()
    detection = detect_industry(artifact, company_name="")
    assert detection.industry == "general"
    assert "empty haystack" in detection.reason
