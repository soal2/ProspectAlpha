"""Explainable confidence: deterministic, LLM-self-report ignored."""

from __future__ import annotations

from riskagent.research.confidence import ConfidenceCalculator
from riskagent.research.gate import VerificationGate
from riskagent.research.models import RiskHypothesis, SourceCard, SourceTier

CALC = ConfidenceCalculator()
GATE = VerificationGate()

_ALL_COMPONENTS = {
    "source_authority",
    "independence",
    "consistency",
    "counter_evidence",
    "freshness",
    "coverage",
    "agent_disagreement",
}


def _hyp(**kw) -> RiskHypothesis:
    payload = {"title": "t", "statement": "s", "dimension": "财务", "origin": "anomaly"}
    payload.update(kw)
    return RiskHypothesis(**payload)


def _fetched(eid: str, tier: SourceTier, **kw) -> SourceCard:
    payload = {
        "evidence_id": eid,
        "url": f"https://s/{eid}",
        "origin": "fetched_page",
        "accessible": True,
        "tier": tier,
    }
    payload.update(kw)
    return SourceCard(**payload)


class TestComponents:
    def test_seven_components_with_reasons(self):
        card = _fetched("e1", SourceTier.tier1, published_at="2025-05-01")
        hyp = _hyp(supporting_evidence_ids=["e1"], refutation_search_done=True)
        gate = GATE.evaluate(hyp, [card])
        assessment = CALC.assess(hyp, [card], gate)
        names = {c.name for c in assessment.components}
        assert names == _ALL_COMPONENTS
        assert all(c.reason for c in assessment.components)
        assert all(0.0 <= c.score <= 1.0 for c in assessment.components)


class TestLLMSelfReportIgnored:
    def test_self_report_does_not_change_level(self):
        card = _fetched("e1", SourceTier.tier1, published_at="2025-05-01")
        hyp = _hyp(supporting_evidence_ids=["e1"], refutation_search_done=True)
        gate = GATE.evaluate(hyp, [card])
        a = CALC.assess(hyp, [card], gate, llm_self_reported=0.01)
        b = CALC.assess(hyp, [card], gate, llm_self_reported=0.99)
        assert a.level == b.level
        assert a.llm_self_reported == 0.01
        assert b.llm_self_reported == 0.99

    def test_self_report_string_recorded(self):
        card = _fetched("e1", SourceTier.tier1)
        hyp = _hyp(supporting_evidence_ids=["e1"], refutation_search_done=True)
        gate = GATE.evaluate(hyp, [card])
        a = CALC.assess(hyp, [card], gate, llm_self_reported="very high")
        assert a.llm_self_reported == "very high"


class TestHardCaps:
    def test_counter_evidence_caps_at_medium(self):
        support = [
            _fetched("e1", SourceTier.tier1, published_at="2025-05-01"),
            _fetched("e2", SourceTier.tier1, published_at="2025-05-02"),
            _fetched("e3", SourceTier.tier1, published_at="2025-05-03"),
        ]
        counter = _fetched("c1", SourceTier.tier1)
        hyp = _hyp(
            supporting_evidence_ids=["e1", "e2", "e3"],
            counter_evidence_ids=["c1"],
            refutation_search_done=True,
        )
        pool = support + [counter]
        gate = GATE.evaluate(hyp, pool)
        assessment = CALC.assess(hyp, pool, gate)
        assert assessment.level in ("low", "medium")
        assert assessment.level != "high"

    def test_failed_gate_caps_at_low(self):
        # Only a tier3 unranked page + no refutation -> gate fails -> cap low.
        card = _fetched("e1", SourceTier.unknown)
        hyp = _hyp(supporting_evidence_ids=["e1"], refutation_search_done=False)
        gate = GATE.evaluate(hyp, [card])
        assert gate.passed is False
        assessment = CALC.assess(hyp, [card], gate)
        assert assessment.level == "low"

    def test_strong_evidence_reaches_high(self):
        support = [
            _fetched("e1", SourceTier.tier1, published_at="2025-05-01"),
            _fetched("e2", SourceTier.tier2, independence_cluster="cl_2", published_at="2025-05-02"),
            _fetched("e3", SourceTier.tier2, independence_cluster="cl_3", published_at="2025-05-03"),
        ]
        hyp = _hyp(
            supporting_evidence_ids=["e1", "e2", "e3"], refutation_search_done=True
        )
        gate = GATE.evaluate(hyp, support)
        assessment = CALC.assess(hyp, support, gate)
        assert assessment.level == "high"
