"""Fact-layer selection: derive the formal conclusion set from a frozen artifact.

The selection is the *only* place that decides which hypotheses/evidence
appear in the brief and the deep report's formal layer.  All other code
reads from :class:`SelectionSummary`.

Material-unresolved is a verdict-derived category, not a HypothesisStatus:
the red-team reviewer records ``material_unresolved`` as a
:class:`RedTeamDecision` against a hypothesis whose status remains
``contested`` in the state machine (the state machine has no
``material_unresolved`` status).  When the latest verdict on a hypothesis
is ``material_unresolved`` (or ``escalate_human``) the hypothesis enters
the formal layer as a "重大待核实事项".

The ``verdict_snapshot_hash`` is a stable digest of the verdicts list so
two builds against the same artifact + identical verdicts hash equally,
while a re-review that adds/changes a verdict forces a new bundle version
on the next build.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping

from riskagent.research.models import (
    HypothesisStatus,
    RedTeamDecision,
    RedTeamVerdict,
    ResearchArtifact,
    RiskHypothesis,
    SourceCard,
    SourceTier,
)

from .models import ExclusionRecord, SelectionSummary


def _latest_verdict_by_hypothesis(
    verdicts: Iterable[RedTeamVerdict],
) -> Mapping[str, RedTeamVerdict]:
    """Pick the most recent verdict per hypothesis.

    Ties on ``created_at`` are broken by the verdict's position in the
    original list (later wins), which is the order the reviewer appended
    them in.  This is deterministic for any given artifact snapshot.
    """

    latest: dict[str, RedTeamVerdict] = {}
    for index, verdict in enumerate(verdicts):
        existing = latest.get(verdict.hypothesis_id)
        if existing is None:
            latest[verdict.hypothesis_id] = verdict
            continue
        if verdict.created_at > existing.created_at:
            latest[verdict.hypothesis_id] = verdict
        elif verdict.created_at == existing.created_at and id(verdict) != id(existing):
            # Same timestamp — keep the one that appeared later in the list.
            latest[verdict.hypothesis_id] = verdict
    return latest


def _hash_verdicts(
    artifact: ResearchArtifact,
    verdicts: Iterable[RedTeamVerdict],
) -> str:
    payload = {
        "artifact_id": artifact.artifact_id,
        "artifact_version": artifact.version,
        "verdicts": [
            {
                "verdict_id": v.verdict_id,
                "hypothesis_id": v.hypothesis_id,
                "decision": v.decision.value,
                "created_at": v.created_at.isoformat(),
            }
            for v in verdicts
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def derive_selection(
    artifact: ResearchArtifact,
    verdicts: Iterable[RedTeamVerdict] | None,
) -> SelectionSummary:
    """Build a :class:`SelectionSummary` from a frozen artifact and its verdicts.

    The artifact MUST be frozen.  An unfrozen artifact is a programming
    error (the renderer reads concurrently-mutable state); callers must
    freeze through :class:`ReportingService` first.
    """

    if not artifact.frozen:
        raise ValueError("derive_selection requires a frozen artifact")

    verdict_list = list(verdicts or [])
    latest = _latest_verdict_by_hypothesis(verdict_list)

    verified: list[str] = []
    material_unresolved: list[str] = []
    contested: list[str] = []
    partially_supported: list[str] = []
    refuted: list[str] = []
    inconclusive: list[str] = []
    exclusions: list[ExclusionRecord] = []

    for hyp in artifact.hypotheses:
        verdict = latest.get(hyp.hypothesis_id)
        decision = verdict.decision if verdict is not None else None

        if decision is RedTeamDecision.request_supplement:
            exclusions.append(
                ExclusionRecord(
                    target_kind="hypothesis",
                    target_id=hyp.hypothesis_id,
                    reason_code="request_supplement_verdict",
                    detail=verdict.rationale if verdict else "",
                )
            )
            continue

        if decision is RedTeamDecision.material_unresolved or decision is RedTeamDecision.escalate_human:
            material_unresolved.append(hyp.hypothesis_id)
            continue

        if hyp.status is HypothesisStatus.verified:
            verified.append(hyp.hypothesis_id)
        elif hyp.status is HypothesisStatus.contested:
            contested.append(hyp.hypothesis_id)
        elif hyp.status is HypothesisStatus.partially_supported:
            partially_supported.append(hyp.hypothesis_id)
        elif hyp.status is HypothesisStatus.refuted:
            refuted.append(hyp.hypothesis_id)
        elif hyp.status is HypothesisStatus.inconclusive:
            inconclusive.append(hyp.hypothesis_id)
        else:
            # ``discovered`` / ``investigating`` are mid-flight; surface as
            # inconclusive so the deep report's appendix can flag them as
            # investigation incomplete.
            inconclusive.append(hyp.hypothesis_id)

    tier_buckets: dict[SourceTier, list[str]] = {
        SourceTier.tier1: [],
        SourceTier.tier2: [],
        SourceTier.tier3: [],
        SourceTier.unknown: [],
    }
    for card in _selected_evidence(artifact):
        tier_buckets[card.tier].append(card.evidence_id)

    rule_finding_ids = _collect_rule_finding_ids(artifact)

    return SelectionSummary(
        verified_hypothesis_ids=verified,
        material_unresolved_hypothesis_ids=material_unresolved,
        contested_hypothesis_ids=contested,
        partially_supported_hypothesis_ids=partially_supported,
        refuted_hypothesis_ids=refuted,
        inconclusive_hypothesis_ids=inconclusive,
        rule_finding_ids=rule_finding_ids,
        exclusions=exclusions,
        tier1_evidence_ids=tier_buckets[SourceTier.tier1],
        tier2_evidence_ids=tier_buckets[SourceTier.tier2],
        tier3_evidence_ids=tier_buckets[SourceTier.tier3],
        unknown_tier_evidence_ids=tier_buckets[SourceTier.unknown],
    )


def _selected_evidence(artifact: ResearchArtifact) -> list[SourceCard]:
    return [card for card in artifact.evidence if card.selection == "selected"]


def _collect_rule_finding_ids(artifact: ResearchArtifact) -> list[str]:
    """Best-effort: rule_ids linked from hypotheses.  Empty if none.

    Phase 1's :class:`RiskHypothesis` carries ``rule_ids`` for hypotheses
    derived from rule hits; Phase 2 surfaces these so the brief can label
    out-of-rule vs rule-driven findings.  The IDs are taken verbatim — the
    reporting layer does not invent or translate them.
    """

    seen: list[str] = []
    for hyp in artifact.hypotheses:
        for rule_id in hyp.rule_ids:
            if rule_id and rule_id not in seen:
                seen.append(rule_id)
    return seen


def snapshot_hash(
    artifact: ResearchArtifact,
    verdicts: Iterable[RedTeamVerdict],
) -> str:
    """Stable hash of (artifact, verdicts) for bundle identity."""

    return _hash_verdicts(artifact, verdicts)
