"""Tests for reporting/selection.py: material_unresolved derivation, exclusions, tiering."""

from __future__ import annotations

import pytest

from riskagent.research.models import (
    HypothesisStatus,
    RedTeamDecision,
    SourceTier,
)

from riskagent.reporting.selection import (
    derive_selection,
    snapshot_hash,
)


def test_verified_hypothesis_without_verdict_goes_to_verified(
    frozen_artifact_factory, hypothesis_factory
):
    hyp = hypothesis_factory(
        hypothesis_id="hyp_v",
        status=HypothesisStatus.verified,
        importance="high",
        with_counter_evidence=True,
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = derive_selection(artifact, [])
    assert selection.verified_hypothesis_ids == ["hyp_v"]
    assert selection.material_unresolved_hypothesis_ids == []


def test_material_unresolved_from_latest_verdict(frozen_artifact_factory, hypothesis_factory, verdict_factory):
    """contested status + material_unresolved verdict ⇒ material_unresolved list."""

    hyp = hypothesis_factory(
        hypothesis_id="hyp_m",
        status=HypothesisStatus.contested,
        importance="high",
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    verdict = verdict_factory(
        hypothesis_id="hyp_m", decision=RedTeamDecision.material_unresolved
    )
    selection = derive_selection(artifact, [verdict])
    assert selection.material_unresolved_hypothesis_ids == ["hyp_m"]
    assert selection.contested_hypothesis_ids == []


def test_request_supplement_verdict_produces_exclusion(
    frozen_artifact_factory, hypothesis_factory, verdict_factory
):
    hyp = hypothesis_factory(
        hypothesis_id="hyp_s",
        status=HypothesisStatus.investigating,
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    verdict = verdict_factory(
        hypothesis_id="hyp_s", decision=RedTeamDecision.request_supplement
    )
    selection = derive_selection(artifact, [verdict])
    assert selection.formal_hypothesis_ids == []
    assert len(selection.exclusions) == 1
    assert selection.exclusions[0].target_id == "hyp_s"
    assert selection.exclusions[0].reason_code == "request_supplement_verdict"


def test_escalate_human_verdict_treated_as_material_unresolved(
    frozen_artifact_factory, hypothesis_factory, verdict_factory
):
    hyp = hypothesis_factory(
        hypothesis_id="hyp_e",
        status=HypothesisStatus.contested,
        importance="high",
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    verdict = verdict_factory(
        hypothesis_id="hyp_e", decision=RedTeamDecision.escalate_human
    )
    selection = derive_selection(artifact, [verdict])
    assert selection.material_unresolved_hypothesis_ids == ["hyp_e"]


def test_latest_verdict_wins_on_same_hypothesis(
    frozen_artifact_factory, hypothesis_factory, verdict_factory
):
    """Two verdicts on one hypothesis: the later one wins.

    Earlier verdict ``request_supplement`` would exclude the hypothesis;
    later verdict ``material_unresolved`` brings it into the formal layer
    as a 重大待核实事项.  The outcome (in formal layer, NOT excluded)
    proves the later verdict was authoritative.
    """

    hyp = hypothesis_factory(
        hypothesis_id="hyp_t",
        status=HypothesisStatus.contested,
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    earlier = verdict_factory(
        hypothesis_id="hyp_t",
        decision=RedTeamDecision.request_supplement,
        minutes_after=0,
    )
    later = verdict_factory(
        hypothesis_id="hyp_t",
        decision=RedTeamDecision.material_unresolved,
        minutes_after=5,
    )
    selection = derive_selection(artifact, [earlier, later])
    assert selection.material_unresolved_hypothesis_ids == ["hyp_t"]
    assert selection.exclusions == []


def test_partially_supported_lands_in_appendix_zone(
    frozen_artifact_factory, hypothesis_factory
):
    hyp = hypothesis_factory(
        hypothesis_id="hyp_p",
        status=HypothesisStatus.partially_supported,
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    selection = derive_selection(artifact, [])
    assert selection.partially_supported_hypothesis_ids == ["hyp_p"]
    assert selection.formal_hypothesis_ids == []


def test_rule_finding_ids_collected_from_hypothesis_rule_links(
    frozen_artifact_factory, hypothesis_factory
):
    hyp = hypothesis_factory(
        hypothesis_id="hyp_r",
        status=HypothesisStatus.verified,
        rule_ids=("RR-001", "RR-002"),
    )
    other = hypothesis_factory(
        hypothesis_id="hyp_r2",
        status=HypothesisStatus.verified,
        rule_ids=("RR-002", "RR-003"),
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp, other])
    selection = derive_selection(artifact, [])
    assert selection.rule_finding_ids == ["RR-001", "RR-002", "RR-003"]


def test_evidence_grouped_by_tier(frozen_artifact_factory, evidence_factory):
    evidence = [
        evidence_factory(evidence_id="t1_a", tier=SourceTier.tier1),
        evidence_factory(evidence_id="t1_b", tier=SourceTier.tier1),
        evidence_factory(evidence_id="t2_a", tier=SourceTier.tier2),
        evidence_factory(evidence_id="t3_a", tier=SourceTier.tier3),
        evidence_factory(evidence_id="uk_a", tier=SourceTier.unknown),
        evidence_factory(evidence_id="excluded", tier=SourceTier.tier1, selection="excluded"),
        evidence_factory(evidence_id="candidate", tier=SourceTier.tier2, selection="candidate"),
    ]
    artifact = frozen_artifact_factory(evidence=evidence)
    selection = derive_selection(artifact, [])
    assert set(selection.tier1_evidence_ids) == {"t1_a", "t1_b"}
    assert set(selection.tier2_evidence_ids) == {"t2_a"}
    assert set(selection.tier3_evidence_ids) == {"t3_a"}
    assert set(selection.unknown_tier_evidence_ids) == {"uk_a"}


def test_snapshot_hash_changes_when_verdict_added(
    frozen_artifact_factory, hypothesis_factory, verdict_factory
):
    hyp = hypothesis_factory(hypothesis_id="hyp_h", status=HypothesisStatus.contested)
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    h0 = snapshot_hash(artifact, [])
    h1 = snapshot_hash(
        artifact,
        [verdict_factory(hypothesis_id="hyp_h", decision=RedTeamDecision.material_unresolved)],
    )
    assert h0 != h1
    assert len(h0) == 64


def test_snapshot_hash_stable_for_same_inputs(
    frozen_artifact_factory, hypothesis_factory, verdict_factory
):
    hyp = hypothesis_factory(hypothesis_id="hyp_h", status=HypothesisStatus.contested)
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    v = verdict_factory(hypothesis_id="hyp_h", decision=RedTeamDecision.material_unresolved)
    assert snapshot_hash(artifact, [v]) == snapshot_hash(artifact, [v])


def test_unfrozen_artifact_rejected(hypothesis_factory):
    """Selection requires a frozen artifact (ProgrammingError guard)."""

    from riskagent.research.models import ResearchArtifact

    hyp = hypothesis_factory(hypothesis_id="hyp_x", status=HypothesisStatus.verified)
    artifact = ResearchArtifact(hypotheses=[hyp])  # not frozen
    with pytest.raises(ValueError, match="frozen"):
        derive_selection(artifact, [])
