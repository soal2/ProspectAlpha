"""Hypothesis lifecycle state machine.

``apply_transition`` is the ONLY entry point that mutates
``RiskHypothesis.status``.  Engine, red team and failure handling all go
through it; LLM output is mapped to transition *attempts* and rejected
attempts are recorded by the caller.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from .models import (
    GateResult,
    HypothesisStatus,
    HypothesisTransition,
    RedTeamDecision,
    ResearchState,
    RiskHypothesis,
    utc_now,
)

TRANSITIONS: dict[HypothesisStatus, frozenset[HypothesisStatus]] = {
    HypothesisStatus.discovered: frozenset(
        {HypothesisStatus.investigating, HypothesisStatus.inconclusive}
    ),
    HypothesisStatus.investigating: frozenset(
        {
            HypothesisStatus.partially_supported,
            HypothesisStatus.contested,
            HypothesisStatus.refuted,
            HypothesisStatus.inconclusive,
        }
    ),
    HypothesisStatus.partially_supported: frozenset(
        {
            HypothesisStatus.verified,
            HypothesisStatus.contested,
            HypothesisStatus.refuted,
            HypothesisStatus.inconclusive,
            HypothesisStatus.investigating,
        }
    ),
    HypothesisStatus.contested: frozenset(
        {
            HypothesisStatus.verified,
            HypothesisStatus.refuted,
            HypothesisStatus.inconclusive,
            HypothesisStatus.investigating,
        }
    ),
    # verified may only be downgraded by the red-team path.
    HypothesisStatus.verified: frozenset({HypothesisStatus.contested}),
    # terminal
    HypothesisStatus.refuted: frozenset(),
    # recovery path
    HypothesisStatus.inconclusive: frozenset({HypothesisStatus.investigating}),
}


class InvalidTransitionError(ValueError):
    pass


def allowed_transitions(status: HypothesisStatus) -> frozenset[HypothesisStatus]:
    return TRANSITIONS[status]


def _find_hypothesis(state: ResearchState, hypothesis_id: str) -> RiskHypothesis:
    if state.working is None:
        raise InvalidTransitionError("no working artifact")
    hyp = state.working.get_hypothesis(hypothesis_id)
    if hyp is None:
        raise InvalidTransitionError(f"unknown hypothesis: {hypothesis_id}")
    return hyp


def apply_transition(
    state: ResearchState,
    hypothesis_id: str,
    to_status: HypothesisStatus,
    *,
    actor: str,
    reason: str,
    evidence_ids: Sequence[str] = (),
    tool_call_ids: Sequence[str] = (),
    gate_snapshot: Optional[GateResult] = None,
    now: Optional[datetime] = None,
) -> HypothesisTransition:
    hyp = _find_hypothesis(state, hypothesis_id)
    from_status = hyp.status
    to_status = HypothesisStatus(to_status)

    if to_status not in TRANSITIONS[from_status]:
        raise InvalidTransitionError(
            f"{hypothesis_id}: {from_status.value} -> {to_status.value} not allowed"
        )

    if to_status is HypothesisStatus.verified:
        snapshot = gate_snapshot or hyp.gate
        if snapshot is None or not snapshot.passed:
            raise InvalidTransitionError(
                f"{hypothesis_id}: verified requires a passing gate snapshot"
            )
        if snapshot.blocking:
            raise InvalidTransitionError(
                f"{hypothesis_id}: verified blocked by {snapshot.blocking}"
            )
        if hyp.importance == "high":
            has_redteam_verified = any(
                v.hypothesis_id == hypothesis_id
                and v.decision is RedTeamDecision.verified
                for v in state.red_team_verdicts
            )
            if not has_redteam_verified:
                raise InvalidTransitionError(
                    f"{hypothesis_id}: high-importance verified requires a "
                    "red-team verdict of 'verified'"
                )
        gate_snapshot = snapshot

    stamp = now or utc_now()
    transition = HypothesisTransition(
        hypothesis_id=hypothesis_id,
        from_status=from_status,
        to_status=to_status,
        at=stamp,
        actor=actor,  # type: ignore[arg-type]
        reason=reason,
        evidence_ids=list(evidence_ids),
        tool_call_ids=list(tool_call_ids),
        gate_snapshot=gate_snapshot,
    )
    hyp.status = to_status
    hyp.updated_at = stamp
    if gate_snapshot is not None:
        hyp.gate = gate_snapshot
    state.transitions.append(transition)
    return transition
