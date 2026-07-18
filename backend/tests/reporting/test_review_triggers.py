"""Tests for reporting/review_triggers.py: 7-rule evaluator."""

from __future__ import annotations

from datetime import timedelta

import pytest

from riskagent.reporting.models import SelectionSummary
from riskagent.reporting.review_triggers import (
    evaluate_review_triggers,
    is_review_required,
)
from riskagent.research.models import (
    ConfidenceAssessment,
    ConfidenceComponent,
    HypothesisStatus,
    RedTeamDecision,
    ResearchCompletionStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _selection(
    *,
    verified: list[str] | None = None,
    material_unresolved: list[str] | None = None,
) -> SelectionSummary:
    """Build a minimal selection that puts the given IDs in the formal layer."""

    return SelectionSummary(
        verified_hypothesis_ids=verified or [],
        material_unresolved_hypothesis_ids=material_unresolved or [],
    )


def _high_conf() -> ConfidenceAssessment:
    return ConfidenceAssessment(
        level="high",
        components=[
            ConfidenceComponent(name="source_authority", score=0.9, reason="tier1"),
            ConfidenceComponent(name="counter_evidence", score=1.0, reason="none"),
        ],
    )


def _medium_conf() -> ConfidenceAssessment:
    return ConfidenceAssessment(
        level="medium",
        components=[
            ConfidenceComponent(name="source_authority", score=0.5, reason="tier2"),
            ConfidenceComponent(name="counter_evidence", score=1.0, reason="none"),
        ],
    )


def _trigger_names(triggers) -> set[str]:
    return {t.trigger_name for t in triggers}


# ---------------------------------------------------------------------------
# 1. external_research_incomplete
# ---------------------------------------------------------------------------


def test_external_research_incomplete_fires_on_incomplete_status(
    frozen_artifact_factory,
):
    artifact = frozen_artifact_factory(
        completion_status=ResearchCompletionStatus.external_research_incomplete,
    )
    triggers = evaluate_review_triggers(artifact, _selection(), [])
    assert "external_research_incomplete" in _trigger_names(triggers)


def test_external_research_incomplete_fires_on_failed_status(
    frozen_artifact_factory,
):
    artifact = frozen_artifact_factory(
        completion_status=ResearchCompletionStatus.failed,
    )
    triggers = evaluate_review_triggers(artifact, _selection(), [])
    assert "external_research_incomplete" in _trigger_names(triggers)


def test_external_research_incomplete_silent_on_complete(frozen_artifact_factory):
    artifact = frozen_artifact_factory(
        completion_status=ResearchCompletionStatus.complete,
    )
    triggers = evaluate_review_triggers(artifact, _selection(), [])
    assert triggers == []


# ---------------------------------------------------------------------------
# 2. red_team_escalate_human
# ---------------------------------------------------------------------------


def test_red_team_escalate_human_fires_on_escalate(
    frozen_artifact_factory, verdict_factory
):
    artifact = frozen_artifact_factory()
    verdicts = [
        verdict_factory(hypothesis_id="hyp_x", decision=RedTeamDecision.escalate_human),
    ]
    triggers = evaluate_review_triggers(artifact, _selection(), verdicts)
    trig = next(t for t in triggers if t.trigger_name == "red_team_escalate_human")
    assert trig.hypothesis_ids == ["hyp_x"]
    assert "1 verdict" in trig.detail


def test_red_team_escalate_human_silent_on_other_decisions(
    frozen_artifact_factory, verdict_factory
):
    artifact = frozen_artifact_factory()
    verdicts = [
        verdict_factory(hypothesis_id="hyp_x", decision=RedTeamDecision.verified),
        verdict_factory(hypothesis_id="hyp_y", decision=RedTeamDecision.refuted),
    ]
    triggers = evaluate_review_triggers(artifact, _selection(), verdicts)
    assert "red_team_escalate_human" not in _trigger_names(triggers)


# ---------------------------------------------------------------------------
# 3. high_importance_nonhigh_confidence
# ---------------------------------------------------------------------------


def test_high_importance_nonhigh_confidence_fires(
    frozen_artifact_factory, hypothesis_factory
):
    hyp = hypothesis_factory(
        hypothesis_id="hyp_h",
        status=HypothesisStatus.verified,
        importance="high",
        confidence_level="medium",
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection(verified=["hyp_h"])
    triggers = evaluate_review_triggers(artifact, selection, [])
    trig = next(t for t in triggers if t.trigger_name == "high_importance_nonhigh_confidence")
    assert "hyp_h" in trig.hypothesis_ids


def test_high_importance_with_high_confidence_is_silent(
    frozen_artifact_factory, hypothesis_factory
):
    hyp = hypothesis_factory(
        hypothesis_id="hyp_h",
        status=HypothesisStatus.verified,
        importance="high",
        confidence_level="high",
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection(verified=["hyp_h"])
    triggers = evaluate_review_triggers(artifact, selection, [])
    assert "high_importance_nonhigh_confidence" not in _trigger_names(triggers)


def test_high_importance_with_no_confidence_fires(
    frozen_artifact_factory, hypothesis_factory
):
    """High-importance hypothesis with no confidence assessment is a review trigger
    — confidence is mandatory for the formal layer."""
    hyp = hypothesis_factory(
        hypothesis_id="hyp_h",
        status=HypothesisStatus.verified,
        importance="high",
        confidence_level=None,
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection(verified=["hyp_h"])
    triggers = evaluate_review_triggers(artifact, selection, [])
    assert "high_importance_nonhigh_confidence" in _trigger_names(triggers)


def test_medium_importance_nonhigh_confidence_is_silent(
    frozen_artifact_factory, hypothesis_factory
):
    """Only high-importance triggers this rule; medium/low stay silent."""
    hyp = hypothesis_factory(
        hypothesis_id="hyp_m",
        status=HypothesisStatus.verified,
        importance="medium",
        confidence_level="medium",
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection(verified=["hyp_m"])
    triggers = evaluate_review_triggers(artifact, selection, [])
    assert "high_importance_nonhigh_confidence" not in _trigger_names(triggers)


def test_high_importance_non_formal_is_silent(
    frozen_artifact_factory, hypothesis_factory
):
    """The rule only inspects the formal layer; an unselected hypothesis
    (e.g. contested) does not fire the trigger even at high importance."""
    hyp = hypothesis_factory(
        hypothesis_id="hyp_c",
        status=HypothesisStatus.contested,
        importance="high",
        confidence_level="low",
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection()  # nothing in formal layer
    triggers = evaluate_review_triggers(artifact, selection, [])
    assert "high_importance_nonhigh_confidence" not in _trigger_names(triggers)


# ---------------------------------------------------------------------------
# 4. authoritative_source_conflict
# ---------------------------------------------------------------------------


def test_authoritative_source_conflict_fires_on_shared_evidence(
    frozen_artifact_factory, hypothesis_factory
):
    """Two formal hypotheses in the same dimension sharing a supporting evidence
    id is an implicit dependency between conclusions."""
    hyp_a = hypothesis_factory(
        hypothesis_id="hyp_a",
        dimension="财务",
        status=HypothesisStatus.verified,
    )
    hyp_b = hypothesis_factory(
        hypothesis_id="hyp_b",
        dimension="财务",
        status=HypothesisStatus.verified,
    )
    # Force overlap on src_a (factory default: src_a, src_b)
    hyp_a.supporting_evidence_ids = ["src_shared", "src_a"]
    hyp_b.supporting_evidence_ids = ["src_shared", "src_b"]
    artifact = frozen_artifact_factory(hypotheses=[hyp_a, hyp_b])
    selection = _selection(verified=["hyp_a", "hyp_b"])
    triggers = evaluate_review_triggers(artifact, selection, [])
    trig = next(t for t in triggers if t.trigger_name == "authoritative_source_conflict")
    assert set(trig.hypothesis_ids) == {"hyp_a", "hyp_b"}


def test_authoritative_source_conflict_silent_across_dimensions(
    frozen_artifact_factory, hypothesis_factory
):
    """Sharing evidence across different dimensions is allowed."""
    hyp_a = hypothesis_factory(
        hypothesis_id="hyp_a",
        dimension="财务",
        status=HypothesisStatus.verified,
    )
    hyp_b = hypothesis_factory(
        hypothesis_id="hyp_b",
        dimension="业务",
        status=HypothesisStatus.verified,
    )
    hyp_a.supporting_evidence_ids = ["src_shared"]
    hyp_b.supporting_evidence_ids = ["src_shared"]
    artifact = frozen_artifact_factory(hypotheses=[hyp_a, hyp_b])
    selection = _selection(verified=["hyp_a", "hyp_b"])
    triggers = evaluate_review_triggers(artifact, selection, [])
    assert "authoritative_source_conflict" not in _trigger_names(triggers)


def test_authoritative_source_conflict_silent_when_no_overlap(
    frozen_artifact_factory, hypothesis_factory
):
    hyp_a = hypothesis_factory(
        hypothesis_id="hyp_a",
        dimension="财务",
        status=HypothesisStatus.verified,
    )
    hyp_b = hypothesis_factory(
        hypothesis_id="hyp_b",
        dimension="财务",
        status=HypothesisStatus.verified,
    )
    hyp_a.supporting_evidence_ids = ["src_a"]
    hyp_b.supporting_evidence_ids = ["src_b"]
    artifact = frozen_artifact_factory(hypotheses=[hyp_a, hyp_b])
    selection = _selection(verified=["hyp_a", "hyp_b"])
    triggers = evaluate_review_triggers(artifact, selection, [])
    assert "authoritative_source_conflict" not in _trigger_names(triggers)


# ---------------------------------------------------------------------------
# 5. major_negative_allegation
# ---------------------------------------------------------------------------


def test_major_negative_allegation_fires_on_default_keyword(
    frozen_artifact_factory, hypothesis_factory
):
    """Without a YAML config the evaluator falls back to the hard-coded
    Chinese keyword list — this test proves the default list works."""
    hyp = hypothesis_factory(
        hypothesis_id="hyp_n",
        status=HypothesisStatus.verified,
    )
    hyp.title = "公司涉嫌财务造假"
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection(verified=["hyp_n"])
    triggers = evaluate_review_triggers(artifact, selection, [], config=None)
    trig = next(t for t in triggers if t.trigger_name == "major_negative_allegation")
    assert "hyp_n" in trig.hypothesis_ids
    assert "财务造假" in trig.detail


def test_major_negative_allegation_matches_statement_too(
    frozen_artifact_factory, hypothesis_factory
):
    """Keyword matching covers both title and statement."""
    hyp = hypothesis_factory(
        hypothesis_id="hyp_n",
        status=HypothesisStatus.verified,
    )
    hyp.title = "公司治理问题"
    hyp.statement = "公司因欺诈行为被监管关注"
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection(verified=["hyp_n"])
    triggers = evaluate_review_triggers(artifact, selection, [])
    assert "major_negative_allegation" in _trigger_names(triggers)


def test_major_negative_allegation_silent_on_neutral_title(
    frozen_artifact_factory, hypothesis_factory
):
    hyp = hypothesis_factory(
        hypothesis_id="hyp_n",
        status=HypothesisStatus.verified,
    )
    hyp.title = "公司应收账款周转率下降"
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection(verified=["hyp_n"])
    triggers = evaluate_review_triggers(artifact, selection, [])
    assert "major_negative_allegation" not in _trigger_names(triggers)


def test_major_negative_allegation_honors_yaml_override(
    frozen_artifact_factory, hypothesis_factory
):
    """A YAML config can extend or replace the keyword list."""
    config = {
        "triggers": [
            {
                "trigger_name": "material_negative_allegation",
                "when": {"hypothesis_title_keywords_any": ["独家丑闻词"]},
            },
        ]
    }
    hyp = hypothesis_factory(
        hypothesis_id="hyp_n",
        status=HypothesisStatus.verified,
    )
    hyp.title = "发生独家丑闻词事件"
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection(verified=["hyp_n"])
    triggers = evaluate_review_triggers(artifact, selection, [], config=config)
    assert "major_negative_allegation" in _trigger_names(triggers)


# ---------------------------------------------------------------------------
# 6. listing_eligibility_risk
# ---------------------------------------------------------------------------


def test_listing_eligibility_risk_fires_on_default_keyword(
    frozen_artifact_factory, hypothesis_factory
):
    hyp = hypothesis_factory(
        hypothesis_id="hyp_l",
        status=HypothesisStatus.verified,
    )
    hyp.title = "公司持续经营能力存在重大不确定性"
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection(verified=["hyp_l"])
    triggers = evaluate_review_triggers(artifact, selection, [])
    trig = next(t for t in triggers if t.trigger_name == "listing_eligibility_risk")
    assert "hyp_l" in trig.hypothesis_ids
    assert "持续经营" in trig.detail


def test_listing_eligibility_risk_silent_on_neutral_title(
    frozen_artifact_factory, hypothesis_factory
):
    hyp = hypothesis_factory(
        hypothesis_id="hyp_l",
        status=HypothesisStatus.verified,
    )
    hyp.title = "公司毛利率上升"
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection(verified=["hyp_l"])
    triggers = evaluate_review_triggers(artifact, selection, [])
    assert "listing_eligibility_risk" not in _trigger_names(triggers)


def test_listing_eligibility_risk_honors_yaml_override(
    frozen_artifact_factory, hypothesis_factory
):
    config = {
        "triggers": [
            {
                "trigger_name": "listing_qualification_risk",
                "when": {"hypothesis_title_keywords_any": ["特别上市警示"]},
            },
        ]
    }
    hyp = hypothesis_factory(
        hypothesis_id="hyp_l",
        status=HypothesisStatus.verified,
    )
    hyp.title = "触发特别上市警示条款"
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection(verified=["hyp_l"])
    triggers = evaluate_review_triggers(artifact, selection, [], config=config)
    assert "listing_eligibility_risk" in _trigger_names(triggers)


# ---------------------------------------------------------------------------
# 7. verdict_status_conflict
# ---------------------------------------------------------------------------


def test_verdict_status_conflict_fires(
    frozen_artifact_factory, hypothesis_factory, verdict_factory
):
    """Red team said material_unresolved but the hypothesis status is verified
    — the state machine and reviewer disagree."""
    hyp = hypothesis_factory(
        hypothesis_id="hyp_v",
        status=HypothesisStatus.verified,
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection(verified=["hyp_v"])
    verdicts = [
        verdict_factory(hypothesis_id="hyp_v", decision=RedTeamDecision.material_unresolved),
    ]
    triggers = evaluate_review_triggers(artifact, selection, verdicts)
    trig = next(t for t in triggers if t.trigger_name == "verdict_status_conflict")
    assert "hyp_v" in trig.hypothesis_ids


def test_verdict_status_conflict_silent_when_status_matches_verdict(
    frozen_artifact_factory, hypothesis_factory, verdict_factory
):
    """Status=verified + decision=verified is consistent — no trigger."""
    hyp = hypothesis_factory(
        hypothesis_id="hyp_v",
        status=HypothesisStatus.verified,
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection(verified=["hyp_v"])
    verdicts = [
        verdict_factory(hypothesis_id="hyp_v", decision=RedTeamDecision.verified),
    ]
    triggers = evaluate_review_triggers(artifact, selection, verdicts)
    assert "verdict_status_conflict" not in _trigger_names(triggers)


def test_verdict_status_conflict_silent_for_non_formal(
    frozen_artifact_factory, hypothesis_factory, verdict_factory
):
    """Only the formal layer matters — a contested hypothesis with a
    material_unresolved verdict is not a conflict (no false positive)."""
    hyp = hypothesis_factory(
        hypothesis_id="hyp_c",
        status=HypothesisStatus.contested,
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection()  # not formal
    verdicts = [
        verdict_factory(hypothesis_id="hyp_c", decision=RedTeamDecision.material_unresolved),
    ]
    triggers = evaluate_review_triggers(artifact, selection, verdicts)
    assert "verdict_status_conflict" not in _trigger_names(triggers)


def test_verdict_status_conflict_uses_latest_verdict(
    frozen_artifact_factory, hypothesis_factory, verdict_factory
):
    """If the latest verdict resolves an earlier escalation, no trigger fires."""
    hyp = hypothesis_factory(
        hypothesis_id="hyp_v",
        status=HypothesisStatus.verified,
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = _selection(verified=["hyp_v"])
    verdicts = [
        verdict_factory(
            hypothesis_id="hyp_v",
            decision=RedTeamDecision.material_unresolved,
            minutes_after=0,
        ),
        verdict_factory(
            hypothesis_id="hyp_v",
            decision=RedTeamDecision.verified,
            minutes_after=5,
        ),
    ]
    triggers = evaluate_review_triggers(artifact, selection, verdicts)
    assert "verdict_status_conflict" not in _trigger_names(triggers)


# ---------------------------------------------------------------------------
# is_review_required helper
# ---------------------------------------------------------------------------


def test_is_review_required_true_when_any_trigger():
    from riskagent.reporting.models import ReviewTriggerRecord

    assert is_review_required(
        [ReviewTriggerRecord(trigger_name="red_team_escalate_human")]
    ) is True


def test_is_review_required_false_on_empty():
    assert is_review_required([]) is False


# ---------------------------------------------------------------------------
# Composition: multiple rules can fire at once
# ---------------------------------------------------------------------------


def test_multiple_triggers_fire_simultaneously(
    frozen_artifact_factory, hypothesis_factory, verdict_factory
):
    """An artifact in bad shape should fire several triggers together."""
    hyp = hypothesis_factory(
        hypothesis_id="hyp_v",
        status=HypothesisStatus.verified,
        importance="high",
        confidence_level="medium",
    )
    hyp.title = "重大财务造假事件"
    artifact = frozen_artifact_factory(
        hypotheses=[hyp],
        completion_status=ResearchCompletionStatus.external_research_incomplete,
    )
    selection = _selection(verified=["hyp_v"])
    verdicts = [
        verdict_factory(hypothesis_id="hyp_v", decision=RedTeamDecision.escalate_human),
    ]
    triggers = evaluate_review_triggers(artifact, selection, verdicts)
    names = _trigger_names(triggers)
    assert {
        "external_research_incomplete",
        "red_team_escalate_human",
        "high_importance_nonhigh_confidence",
        "major_negative_allegation",
    } <= names
    assert is_review_required(triggers) is True
