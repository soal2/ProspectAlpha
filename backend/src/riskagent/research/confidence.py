"""Explainable confidence scoring.

The level is computed deterministically from seven components; the LLM's
self-reported confidence is recorded verbatim for audit but carries **zero
weight** (proof: two assessments differing only in ``llm_self_reported``
produce the same level).  Hard caps: any unresolved counter-evidence caps the
level at ``medium``; a failed gate caps it at ``low``.  We report the level
plus per-component reasons — never a fake-precise numeric score.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .gate import is_countable
from .models import (
    ConfidenceAssessment,
    ConfidenceComponent,
    GateResult,
    RiskHypothesis,
    SourceCard,
    SourceTier,
)

_LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}
_ORDER_LEVEL = {0: "low", 1: "medium", 2: "high"}


def _cap(level: str, ceiling: str) -> str:
    return _ORDER_LEVEL[min(_LEVEL_ORDER[level], _LEVEL_ORDER[ceiling])]


class ConfidenceCalculator:
    def assess(
        self,
        hypothesis: RiskHypothesis,
        evidence: Sequence[SourceCard],
        gate: GateResult,
        *,
        data_cutoff_date: Optional[str] = None,
        agent_disagreement: Optional[float] = None,
        llm_self_reported: Optional[float | str] = None,
    ) -> ConfidenceAssessment:
        by_id = {card.evidence_id: card for card in evidence}
        support = [by_id[e] for e in hypothesis.supporting_evidence_ids if e in by_id]
        counter = [by_id[e] for e in hypothesis.counter_evidence_ids if e in by_id]
        countable = [c for c in support if is_countable(c)]

        components = [
            self._source_authority(countable),
            self._independence(gate),
            self._consistency(countable, counter),
            self._counter_evidence(counter),
            self._freshness(countable, data_cutoff_date),
            self._coverage(countable),
            self._agent_disagreement(agent_disagreement),
        ]

        mean = sum(c.score for c in components) / len(components)
        if mean >= 0.7:
            level = "high"
        elif mean >= 0.45:
            level = "medium"
        else:
            level = "low"

        # Hard caps — these are floors on caution, never overridden by score.
        if any(is_countable(c) for c in counter):
            level = _cap(level, "medium")
        if not gate.passed:
            level = _cap(level, "low")

        return ConfidenceAssessment(
            level=level,  # type: ignore[arg-type]
            components=components,
            llm_self_reported=llm_self_reported,
        )

    # ------------------------------------------------------------------
    # components

    @staticmethod
    def _source_authority(countable: Sequence[SourceCard]) -> ConfidenceComponent:
        has_t1 = any(c.tier is SourceTier.tier1 for c in countable)
        has_t2 = any(c.tier is SourceTier.tier2 for c in countable)
        if has_t1:
            score, reason = 1.0, "supported by a tier1 authoritative source"
        elif has_t2:
            score, reason = 0.6, "supported by tier2 media/database only"
        elif countable:
            score, reason = 0.25, "supported only by unranked (tier3) pages"
        else:
            score, reason = 0.0, "no countable fetched-page support"
        return ConfidenceComponent(name="source_authority", score=score, reason=reason)

    @staticmethod
    def _independence(gate: GateResult) -> ConfidenceComponent:
        clusters = gate.independent_tier2_clusters + (1 if gate.tier1_count else 0)
        if gate.tier1_count >= 1 and gate.independent_tier2_clusters >= 1:
            score = 1.0
        elif clusters >= 2:
            score = 0.8
        elif clusters == 1:
            score = 0.4
        else:
            score = 0.0
        return ConfidenceComponent(
            name="independence",
            score=score,
            reason=f"{gate.tier1_count} tier1 + {gate.independent_tier2_clusters} independent tier2 cluster(s)",
        )

    @staticmethod
    def _consistency(
        countable: Sequence[SourceCard], counter: Sequence[SourceCard]
    ) -> ConfidenceComponent:
        n_support = len(countable)
        n_counter = sum(1 for c in counter if is_countable(c))
        if n_support == 0:
            return ConfidenceComponent(
                name="consistency", score=0.0, reason="no countable support to assess"
            )
        if n_counter == 0:
            return ConfidenceComponent(
                name="consistency",
                score=0.9,
                reason=f"{n_support} supporting card(s), no contradicting fetched pages",
            )
        ratio = n_support / (n_support + n_counter)
        return ConfidenceComponent(
            name="consistency",
            score=round(0.2 + 0.6 * ratio, 3),
            reason=f"{n_support} supporting vs {n_counter} contradicting fetched page(s)",
        )

    @staticmethod
    def _counter_evidence(counter: Sequence[SourceCard]) -> ConfidenceComponent:
        countable_counter = [c for c in counter if is_countable(c)]
        if not countable_counter:
            return ConfidenceComponent(
                name="counter_evidence", score=1.0, reason="no countable counter-evidence"
            )
        has_t1 = any(c.tier is SourceTier.tier1 for c in countable_counter)
        return ConfidenceComponent(
            name="counter_evidence",
            score=0.1 if has_t1 else 0.3,
            reason=(
                "authoritative counter-evidence present"
                if has_t1
                else f"{len(countable_counter)} counter-evidence page(s) present"
            ),
        )

    @staticmethod
    def _freshness(
        countable: Sequence[SourceCard], data_cutoff_date: Optional[str]
    ) -> ConfidenceComponent:
        if not countable:
            return ConfidenceComponent(
                name="freshness", score=0.0, reason="no countable support"
            )
        dated = [c for c in countable if c.published_at]
        if not dated:
            return ConfidenceComponent(
                name="freshness",
                score=0.3,
                reason="all supporting pages are undated",
            )
        newest = max(c.published_at for c in dated)  # type: ignore[type-var]
        return ConfidenceComponent(
            name="freshness",
            score=0.8,
            reason=f"{len(dated)}/{len(countable)} supporting pages dated (newest {newest})",
        )

    @staticmethod
    def _coverage(countable: Sequence[SourceCard]) -> ConfidenceComponent:
        n = len(countable)
        score = min(1.0, n / 3.0)
        return ConfidenceComponent(
            name="coverage",
            score=round(score, 3),
            reason=f"{n} countable supporting page(s)",
        )

    @staticmethod
    def _agent_disagreement(
        agent_disagreement: Optional[float],
    ) -> ConfidenceComponent:
        if agent_disagreement is None:
            return ConfidenceComponent(
                name="agent_disagreement",
                score=0.7,
                reason="no cross-agent disagreement signal recorded",
            )
        disagreement = max(0.0, min(1.0, agent_disagreement))
        return ConfidenceComponent(
            name="agent_disagreement",
            score=round(1.0 - disagreement, 3),
            reason=f"agent disagreement level {disagreement:.2f}",
        )
