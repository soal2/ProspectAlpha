"""Tests for reporting/service.py: bundle assembly + R5/R6 assertions."""

from __future__ import annotations

from pathlib import Path

import pytest

from riskagent.reporting.errors import ReportBuildError
from riskagent.reporting.service import (
    BundleBuildRejected,
    BuildRequest,
    ReportingService,
    make_service,
)
from riskagent.research.models import (
    HypothesisStatus,
    RedTeamDecision,
    ResearchCompletionStatus,
)


_CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "report_templates"


def _service() -> ReportingService:
    return make_service(_CONFIG_DIR)


def _clean(hyp):
    """Clear factory default supporting/counter IDs so R6 doesn't fire spuriously."""
    hyp.supporting_evidence_ids = []
    hyp.counter_evidence_ids = []
    return hyp


def _request(
    artifact,
    *,
    run_id: str = "run_test",
    company_name: str = "示例公司",
    business_description: str = "",
    stock_code: str = "01234.HK",
    external_cutoff_date: str = "2024-06-30",
    analysis_date: str = "2024-07-15",
    verdicts=None,
    run_input_payload=None,
) -> BuildRequest:
    return BuildRequest(
        run_id=run_id,
        artifact=artifact,
        verdicts=verdicts or [],
        company_name=company_name,
        business_description=business_description,
        stock_code=stock_code,
        external_cutoff_date=external_cutoff_date,
        analysis_date=analysis_date,
        run_input_payload=run_input_payload,
    )


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_build_bundle_happy_path(
    frozen_artifact_factory, hypothesis_factory
):
    """A minimal complete artifact produces a frozen v1 bundle."""

    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            importance="high",
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(
        hypotheses=[hyp],
        completion_status=ResearchCompletionStatus.complete,
    )
    bundle = _service().build_bundle(_request(artifact))
    assert bundle.run_id == "run_test"
    assert bundle.report_version == 1
    assert bundle.research_artifact_version == artifact.version
    assert bundle.frozen is True
    assert bundle.review_state == "auto_completed"
    assert bundle.review_triggers == []
    # Both modules rendered
    assert bundle.decision_brief.module_kind == "decision_brief"
    assert bundle.deep_research.module_kind == "deep_research"
    # Templates are referenced with sha256 hashes
    assert bundle.brief_template.content_sha256
    assert bundle.deep_template.content_sha256
    # Industry defaults to general when no signal
    assert bundle.industry_profile.detected == "general"


def test_build_bundle_is_strictly_frozen(
    frozen_artifact_factory, hypothesis_factory
):
    """Once frozen, the bundle rejects attribute writes."""

    from riskagent.reporting.models import FrozenBundleError

    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _service().build_bundle(_request(artifact))
    with pytest.raises(FrozenBundleError):
        bundle.review_state = "draft"


def test_build_bundle_records_industry_biotech(
    frozen_artifact_factory, hypothesis_factory
):
    """Industry detection flows through to the bundle profile."""

    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    hyp.title = "在研单克隆抗体临床进展"
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _service().build_bundle(
        _request(artifact, company_name="某生物制药股份")
    )
    assert bundle.industry_profile.detected == "biotech"
    assert bundle.industry_profile.fragment is not None


# ---------------------------------------------------------------------------
# R7 frozen-artifact gate
# ---------------------------------------------------------------------------


def test_build_rejects_unfrozen_artifact(
    frozen_artifact_factory, hypothesis_factory
):
    """Unfrozen artifact is a hard failure (R7)."""

    hyp = hypothesis_factory(
        hypothesis_id="hyp_v",
        status=HypothesisStatus.verified,
    )
    # Bypass the freeze() call by constructing manually
    from riskagent.research.models import (
        CoverageTask,
        ExternalSearchPolicy,
        ResearchArtifact,
    )

    artifact = ResearchArtifact(
        artifact_id="art_raw",
        version=1,
        based_on_version=None,
        completion_status=ResearchCompletionStatus.complete,
        policy=ExternalSearchPolicy.public_only,
        plan=[CoverageTask(dimension="财务", status="done")],
        hypotheses=[hyp],
        evidence=[],
    )
    assert not artifact.frozen
    with pytest.raises(ReportBuildError, match="R7"):
        _service().build_bundle(_request(artifact))


# ---------------------------------------------------------------------------
# BundleBuildRejected for failed artifact
# ---------------------------------------------------------------------------


def test_build_rejects_failed_artifact(frozen_artifact_factory):
    """completion_status=failed surfaces as BundleBuildRejected (→ HTTP 409)."""

    artifact = frozen_artifact_factory(
        completion_status=ResearchCompletionStatus.failed,
    )
    with pytest.raises(BundleBuildRejected) as exc_info:
        _service().build_bundle(_request(artifact))
    assert exc_info.value.reason_code == "artifact_failed"


# ---------------------------------------------------------------------------
# R5 subset assertion
# ---------------------------------------------------------------------------


def test_build_renders_brief_subset_of_deep(
    frozen_artifact_factory, hypothesis_factory
):
    """Sanity check: a clean verified hypothesis appears in both modules."""

    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_in_both",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _service().build_bundle(_request(artifact))
    assert "hyp_in_both" in bundle.decision_brief.hypothesis_ids_visible
    assert "hyp_in_both" in bundle.deep_research.hypothesis_ids_visible


def test_build_rejects_counter_evidence_pointing_at_void(
    frozen_artifact_factory, hypothesis_factory
):
    """A verified hypothesis whose counter_evidence_id is missing from the
    artifact's evidence list triggers R6."""

    hyp = hypothesis_factory(
        hypothesis_id="hyp_v",
        status=HypothesisStatus.verified,
        with_counter_evidence=True,
    )
    # ``with_counter_evidence=True`` adds 'src_c' to counter_evidence_ids but
    # we don't supply that evidence card → R6 should fire.
    hyp.supporting_evidence_ids = []  # clear supporting default
    # Confirm counter exists but evidence card absent
    assert hyp.counter_evidence_ids == ["src_c"]
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    with pytest.raises(ReportBuildError, match="R6"):
        _service().build_bundle(_request(artifact))


# ---------------------------------------------------------------------------
# Review triggers flow into the bundle
# ---------------------------------------------------------------------------


def test_build_bundle_marks_review_required_on_external_incomplete(
    frozen_artifact_factory, hypothesis_factory
):
    """external_research_incomplete fires a trigger and flips review_state."""

    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(
        hypotheses=[hyp],
        completion_status=ResearchCompletionStatus.external_research_incomplete,
    )
    bundle = _service().build_bundle(_request(artifact))
    assert bundle.review_state == "review_required"
    trig_names = {t.trigger_name for t in bundle.review_triggers}
    assert "external_research_incomplete" in trig_names


def test_build_bundle_marks_review_required_on_red_team_escalation(
    frozen_artifact_factory, hypothesis_factory, verdict_factory
):
    """escalate_human verdict flips the bundle to review_required."""

    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_e",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    verdicts = [
        verdict_factory(hypothesis_id="hyp_e", decision=RedTeamDecision.escalate_human),
    ]
    bundle = _service().build_bundle(_request(artifact, verdicts=verdicts))
    assert bundle.review_state == "review_required"
    trig_names = {t.trigger_name for t in bundle.review_triggers}
    assert "red_team_escalate_human" in trig_names


# ---------------------------------------------------------------------------
# Determinism: same inputs → same hash
# ---------------------------------------------------------------------------


def test_build_bundle_is_deterministic_for_same_inputs(
    frozen_artifact_factory, hypothesis_factory
):
    """Two builds against identical inputs produce identical snapshot hashes."""

    def build():
        hyp = _clean(
            hypothesis_factory(
                hypothesis_id="hyp_v",
                status=HypothesisStatus.verified,
                confidence_level="high",
            )
        )
        artifact = frozen_artifact_factory(hypotheses=[hyp])
        return _service().build_bundle(_request(artifact))

    a = build()
    b = build()
    assert a.verdict_snapshot_hash == b.verdict_snapshot_hash
    assert a.brief_template.content_sha256 == b.brief_template.content_sha256
    assert a.deep_template.content_sha256 == b.deep_template.content_sha256


# ---------------------------------------------------------------------------
# Degradations: D1/D2 surface when input_payload lacks structured_data
# ---------------------------------------------------------------------------


def test_build_bundle_propagates_d1_d2_when_no_structured_data(
    frozen_artifact_factory, hypothesis_factory
):
    """Without structured_data, financial_trend (D1) and peer_comparison (D2)
    degrade — both must surface as persisted notes on the bundle."""

    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _service().build_bundle(_request(artifact, run_input_payload=None))
    codes = {d.code.value for d in bundle.degradations}
    assert "D1" in codes
    assert "D2" in codes
