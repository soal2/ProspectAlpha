"""VerificationGate: the structural bar before ``verified``."""

from __future__ import annotations

from riskagent.research.gate import (
    BLOCK_UNRESOLVED_COUNTER,
    VerificationGate,
)
from riskagent.research.models import RiskHypothesis, SourceCard, SourceTier


def _hyp(**kw) -> RiskHypothesis:
    payload = {
        "title": "t",
        "statement": "s",
        "dimension": "法务监管",
        "origin": "anomaly",
    }
    payload.update(kw)
    return RiskHypothesis(**payload)


def _fetched(evidence_id: str, tier: SourceTier, **kw) -> SourceCard:
    payload = {
        "evidence_id": evidence_id,
        "url": f"https://site/{evidence_id}",
        "origin": "fetched_page",
        "accessible": True,
        "tier": tier,
    }
    payload.update(kw)
    return SourceCard(**payload)


def _snippet(evidence_id: str, tier: SourceTier, **kw) -> SourceCard:
    payload = {
        "evidence_id": evidence_id,
        "url": f"https://site/{evidence_id}",
        "origin": "search_snippet",
        "tier": tier,
    }
    payload.update(kw)
    return SourceCard(**payload)


GATE = VerificationGate()


class TestThreshold:
    def test_single_tier1_passes(self):
        card = _fetched("e1", SourceTier.tier1)
        hyp = _hyp(supporting_evidence_ids=["e1"], refutation_search_done=True)
        result = GATE.evaluate(hyp, [card])
        assert result.passed is True
        assert result.tier1_count == 1

    def test_two_independent_tier2_passes(self):
        c1 = _fetched("e1", SourceTier.tier2, independence_cluster="cl_1")
        c2 = _fetched("e2", SourceTier.tier2, independence_cluster="cl_2")
        hyp = _hyp(supporting_evidence_ids=["e1", "e2"], refutation_search_done=True)
        result = GATE.evaluate(hyp, [c1, c2])
        assert result.passed is True
        assert result.independent_tier2_clusters == 2

    def test_two_same_cluster_tier2_fails(self):
        c1 = _fetched("e1", SourceTier.tier2, independence_cluster="cl_1")
        c2 = _fetched("e2", SourceTier.tier2, independence_cluster="cl_1")
        hyp = _hyp(supporting_evidence_ids=["e1", "e2"], refutation_search_done=True)
        result = GATE.evaluate(hyp, [c1, c2])
        assert result.passed is False
        assert result.independent_tier2_clusters == 1

    def test_refutation_search_required(self):
        card = _fetched("e1", SourceTier.tier1)
        hyp = _hyp(supporting_evidence_ids=["e1"], refutation_search_done=False)
        result = GATE.evaluate(hyp, [card])
        assert result.passed is False
        assert result.refutation_search_done is False

    def test_refutation_override_param(self):
        card = _fetched("e1", SourceTier.tier1)
        hyp = _hyp(supporting_evidence_ids=["e1"], refutation_search_done=False)
        result = GATE.evaluate(hyp, [card], refutation_search_done=True)
        assert result.passed is True


class TestSnippetExclusion:
    def test_pure_snippet_never_counts(self):
        # A tier1-domain *search snippet* is structurally excluded.
        card = _snippet("e1", SourceTier.tier1)
        hyp = _hyp(supporting_evidence_ids=["e1"], refutation_search_done=True)
        result = GATE.evaluate(hyp, [card])
        assert result.tier1_count == 0
        assert result.passed is False

    def test_inaccessible_fetch_never_counts(self):
        card = _fetched(
            "e1", SourceTier.tier1, accessible=False, inaccessible_reason="http_403"
        )
        hyp = _hyp(supporting_evidence_ids=["e1"], refutation_search_done=True)
        result = GATE.evaluate(hyp, [card])
        assert result.tier1_count == 0
        assert result.passed is False


class TestTimeBoundary:
    def test_undated_cannot_support_time_sensitive(self):
        card = _fetched("e1", SourceTier.tier1, undated=True)
        hyp = _hyp(
            supporting_evidence_ids=["e1"],
            refutation_search_done=True,
            time_sensitive=True,
        )
        result = GATE.evaluate(hyp, [card])
        assert result.tier1_count == 0
        assert result.passed is False

    def test_undated_ok_for_non_time_sensitive(self):
        card = _fetched("e1", SourceTier.tier1, undated=True)
        hyp = _hyp(
            supporting_evidence_ids=["e1"],
            refutation_search_done=True,
            time_sensitive=False,
        )
        result = GATE.evaluate(hyp, [card])
        assert result.passed is True

    def test_post_cutoff_not_counted_toward_pre_cutoff_claim(self):
        card = _fetched("e1", SourceTier.tier1, published_at="2025-12-01")
        hyp = _hyp(supporting_evidence_ids=["e1"], refutation_search_done=True)
        result = GATE.evaluate(hyp, [card], data_cutoff_date="2025-06-30")
        assert result.tier1_count == 0
        assert result.passed is False

    def test_pre_cutoff_counted(self):
        card = _fetched("e1", SourceTier.tier1, published_at="2025-05-01")
        hyp = _hyp(supporting_evidence_ids=["e1"], refutation_search_done=True)
        result = GATE.evaluate(hyp, [card], data_cutoff_date="2025-06-30")
        assert result.passed is True


class TestBlocking:
    def test_unresolved_counter_evidence_blocks(self):
        support = _fetched("e1", SourceTier.tier1)
        counter = _fetched("c1", SourceTier.tier1)
        hyp = _hyp(
            supporting_evidence_ids=["e1"],
            counter_evidence_ids=["c1"],
            refutation_search_done=True,
        )
        result = GATE.evaluate(hyp, [support, counter])
        # Threshold is met, but verified must be blocked.
        assert result.passed is True
        assert BLOCK_UNRESOLVED_COUNTER in result.blocking

    def test_snippet_counter_does_not_block(self):
        support = _fetched("e1", SourceTier.tier1)
        counter = _snippet("c1", SourceTier.tier1)
        hyp = _hyp(
            supporting_evidence_ids=["e1"],
            counter_evidence_ids=["c1"],
            refutation_search_done=True,
        )
        result = GATE.evaluate(hyp, [support, counter])
        assert result.blocking == []

    def test_extra_blocking_flags_passthrough(self):
        support = _fetched("e1", SourceTier.tier1)
        hyp = _hyp(supporting_evidence_ids=["e1"], refutation_search_done=True)
        result = GATE.evaluate(hyp, [support], extra_blocking=["entity_match_doubt"])
        assert "entity_match_doubt" in result.blocking


class TestHighImportanceInsufficient:
    def test_high_importance_insufficient_does_not_pass(self):
        # High-importance hypothesis with only a snippet -> gate does not pass,
        # which downstream maps to material-unresolved / review_required.
        card = _snippet("e1", SourceTier.tier1)
        hyp = _hyp(
            importance="high",
            supporting_evidence_ids=["e1"],
            refutation_search_done=True,
        )
        result = GATE.evaluate(hyp, [card])
        assert result.passed is False
