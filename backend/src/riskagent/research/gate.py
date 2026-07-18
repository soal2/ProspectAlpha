"""Verification gate: the structural bar a hypothesis must clear before it can
be promoted to ``verified``.

The gate is deliberately mechanical — no LLM, no prompt.  Proof test (b)
("a single search snippet / weak source never becomes a formal risk") is
enforced *here*, not by instructing a model: only ``origin="fetched_page"``
cards that are ``accessible`` are ever counted.  Passing the gate is
*necessary but not sufficient*: a non-empty ``blocking`` list (unresolved
authoritative counter-evidence, entity-match doubt, material conflict) still
prevents the ``verified`` transition (enforced again in the state machine).
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .models import GateResult, RiskHypothesis, SourceCard, SourceTier

# Blocking reasons the state machine treats as hard stops on ``verified``.
BLOCK_UNRESOLVED_COUNTER = "unresolved_counter_evidence"
BLOCK_ENTITY_MATCH = "entity_match_doubt"
BLOCK_MATERIAL_CONFLICT = "material_conflict"


def is_countable(card: SourceCard) -> bool:
    """A card counts as evidence only if it is a fetched, readable page.

    Search snippets (``origin="search_snippet"``) and inaccessible fetches
    (robots/paywall/SSRF-rejected/oversize/timeout) are structurally excluded.
    """
    return card.origin == "fetched_page" and card.accessible


class VerificationGate:
    def evaluate(
        self,
        hypothesis: RiskHypothesis,
        evidence: Sequence[SourceCard],
        *,
        data_cutoff_date: Optional[str] = None,
        refutation_search_done: Optional[bool] = None,
        extra_blocking: Iterable[str] = (),
    ) -> GateResult:
        by_id = {card.evidence_id: card for card in evidence}
        support = [
            by_id[eid] for eid in hypothesis.supporting_evidence_ids if eid in by_id
        ]
        counter = [
            by_id[eid] for eid in hypothesis.counter_evidence_ids if eid in by_id
        ]

        reasons: list[str] = []
        countable_support: list[SourceCard] = []
        for card in support:
            if not is_countable(card):
                reasons.append(
                    f"{card.evidence_id}: excluded (search snippet or inaccessible page)"
                )
                continue
            if hypothesis.time_sensitive and card.undated:
                reasons.append(
                    f"{card.evidence_id}: excluded (undated cannot support a time-sensitive claim)"
                )
                continue
            if (
                data_cutoff_date
                and card.published_at
                and card.published_at > data_cutoff_date
            ):
                reasons.append(
                    f"{card.evidence_id}: excluded (post-cutoff update, not counted toward a pre-cutoff claim)"
                )
                continue
            countable_support.append(card)

        tier1_count = sum(
            1 for c in countable_support if c.tier is SourceTier.tier1
        )
        tier2_clusters = {
            (c.independence_cluster or c.evidence_id)
            for c in countable_support
            if c.tier is SourceTier.tier2
        }
        independent_tier2 = len(tier2_clusters)

        refutation_done = (
            hypothesis.refutation_search_done
            if refutation_search_done is None
            else refutation_search_done
        )

        threshold_met = tier1_count >= 1 or independent_tier2 >= 2
        passed = threshold_met and refutation_done

        if tier1_count >= 1:
            reasons.append(f"{tier1_count} tier1 authoritative source(s)")
        elif independent_tier2 >= 2:
            reasons.append(f"{independent_tier2} independent tier2 clusters")
        else:
            reasons.append(
                "insufficient evidence: need >=1 tier1 or >=2 independent tier2 clusters"
            )
        if not refutation_done:
            reasons.append("refutation search not yet performed")

        blocking: list[str] = []
        for flag in extra_blocking:
            if flag and flag not in blocking:
                blocking.append(flag)
        if any(is_countable(c) for c in counter):
            if BLOCK_UNRESOLVED_COUNTER not in blocking:
                blocking.append(BLOCK_UNRESOLVED_COUNTER)
            reasons.append("countable counter-evidence present and unresolved")

        return GateResult(
            passed=passed,
            tier1_count=tier1_count,
            independent_tier2_clusters=independent_tier2,
            refutation_search_done=refutation_done,
            blocking=blocking,
            reasons=reasons,
        )
