"""Review-trigger evaluator: 7 rules, config-driven, fully derived.

A bundle's ``review_required`` flag (and the corresponding list of fired
triggers) is computed *after* the selection is derived from the frozen
artifact + verdicts.  Every rule's inputs come from already-observable
fields — Phase 2 deliberately does not invent new signals.

The 7 triggers (matching ``review_triggers.yaml``):

1. ``external_research_incomplete`` — artifact ``completion_status`` ∈
   {external_research_incomplete, failed}.
2. ``red_team_escalate_human`` — any verdict with ``decision=escalate_human``.
3. ``high_importance_nonhigh_confidence`` — any verified/material_unresolved
   hypothesis with ``importance=high`` but ``confidence.level != high``.
4. ``authoritative_source_conflict`` — two verified hypotheses in the same
   dimension whose supporting evidence lists overlap (same source).
5. ``major_negative_allegation`` — a verified/material_unresolved hypothesis
   whose title contains one of the configured negative-allegation keywords.
6. ``listing_eligibility_risk`` — same shape as #5 but with listing-risk
   keywords.
7. ``verdict_status_conflict`` — a hypothesis whose latest verdict is
   ``material_unresolved`` but whose status is ``verified`` (the state
   machine and red team disagree).
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping

from riskagent.research.models import (
    ConfidenceAssessment,
    HypothesisStatus,
    RedTeamDecision,
    RedTeamVerdict,
    ResearchArtifact,
    ResearchCompletionStatus,
    RiskHypothesis,
)

from .models import ReviewTriggerRecord, SelectionSummary


_DEFAULT_NEGATIVE_ALLEGATION_KEYWORDS: tuple[str, ...] = (
    "财务造假",
    "欺诈",
    "立案调查",
    "行政处罚",
    "退市",
    "暂停上市",
    "控股股东占用",
    "重大诉讼",
)

_DEFAULT_LISTING_RISK_KEYWORDS: tuple[str, ...] = (
    "上市资格",
    "持续经营",
    "净资产为负",
    "重大违规",
    "强制退市",
)


def evaluate_review_triggers(
    artifact: ResearchArtifact,
    selection: SelectionSummary,
    verdicts: Iterable[RedTeamVerdict],
    *,
    config: Mapping[str, object] | None = None,
) -> list[ReviewTriggerRecord]:
    """Return the list of fired triggers; empty list means auto-completion is OK."""

    verdict_list = list(verdicts)
    latest_verdict_by_hyp = _latest_verdict_by_hypothesis(verdict_list)
    formal_ids = set(selection.formal_hypothesis_ids)
    formal_hypotheses = [
        h for h in artifact.hypotheses if h.hypothesis_id in formal_ids
    ]

    triggers: list[ReviewTriggerRecord] = []
    triggers.extend(_external_research_incomplete(artifact))
    triggers.extend(_red_team_escalate_human(verdict_list))
    triggers.extend(_high_importance_nonhigh_confidence(formal_hypotheses))
    triggers.extend(_authoritative_source_conflict(artifact, formal_hypotheses))
    triggers.extend(
        _major_negative_allegation(formal_hypotheses, config)
    )
    triggers.extend(_listing_eligibility_risk(formal_hypotheses, config))
    triggers.extend(
        _verdict_status_conflict(artifact, latest_verdict_by_hyp, formal_ids)
    )
    return triggers


def is_review_required(triggers: list[ReviewTriggerRecord]) -> bool:
    return bool(triggers)


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------


def _external_research_incomplete(
    artifact: ResearchArtifact,
) -> Iterable[ReviewTriggerRecord]:
    if artifact.completion_status in (
        ResearchCompletionStatus.external_research_incomplete,
        ResearchCompletionStatus.failed,
    ):
        yield ReviewTriggerRecord(
            trigger_name="external_research_incomplete",
            detail=f"artifact.completion_status={artifact.completion_status.value}",
        )


def _red_team_escalate_human(
    verdicts: list[RedTeamVerdict],
) -> Iterable[ReviewTriggerRecord]:
    escalations = [v for v in verdicts if v.decision is RedTeamDecision.escalate_human]
    if escalations:
        yield ReviewTriggerRecord(
            trigger_name="red_team_escalate_human",
            detail=f"{len(escalations)} verdict(s) escalated to human",
            hypothesis_ids=[v.hypothesis_id for v in escalations],
        )


def _high_importance_nonhigh_confidence(
    formal_hypotheses: list[RiskHypothesis],
) -> Iterable[ReviewTriggerRecord]:
    matches = [
        h
        for h in formal_hypotheses
        if h.importance == "high"
        and (h.confidence is None or h.confidence.level != "high")
    ]
    if matches:
        yield ReviewTriggerRecord(
            trigger_name="high_importance_nonhigh_confidence",
            detail=f"{len(matches)} high-importance formal hypotheses have non-high confidence",
            hypothesis_ids=[h.hypothesis_id for h in matches],
        )


def _authoritative_source_conflict(
    artifact: ResearchArtifact,
    formal_hypotheses: list[RiskHypothesis],
) -> Iterable[ReviewTriggerRecord]:
    """Two formal hypotheses in the same dimension share at least one
    supporting evidence id — implicit dependency between conclusions.
    """

    by_dimension: dict[str, list[RiskHypothesis]] = {}
    for h in formal_hypotheses:
        by_dimension.setdefault(h.dimension, []).append(h)
    for dimension, hyps in by_dimension.items():
        if len(hyps) < 2:
            continue
        seen_evidence: dict[str, str] = {}
        for h in hyps:
            for eid in h.supporting_evidence_ids:
                previous = seen_evidence.get(eid)
                if previous is not None and previous != h.hypothesis_id:
                    yield ReviewTriggerRecord(
                        trigger_name="authoritative_source_conflict",
                        detail=(
                            f"dimension={dimension}: hypotheses "
                            f"{previous!r} and {h.hypothesis_id!r} share evidence {eid!r}"
                        ),
                        hypothesis_ids=[previous, h.hypothesis_id],
                    )
                    return  # one firing per dimension is enough
                seen_evidence[eid] = h.hypothesis_id


def _major_negative_allegation(
    formal_hypotheses: list[RiskHypothesis],
    config: Mapping[str, object] | None,
) -> Iterable[ReviewTriggerRecord]:
    keywords = _keyword_list(config, "material_negative_allegation")
    yield from _title_keyword_trigger(
        formal_hypotheses,
        keywords,
        trigger_name="major_negative_allegation",
    )


def _listing_eligibility_risk(
    formal_hypotheses: list[RiskHypothesis],
    config: Mapping[str, object] | None,
) -> Iterable[ReviewTriggerRecord]:
    keywords = _keyword_list(config, "listing_qualification_risk")
    yield from _title_keyword_trigger(
        formal_hypotheses,
        keywords,
        trigger_name="listing_eligibility_risk",
    )


def _verdict_status_conflict(
    artifact: ResearchArtifact,
    latest_verdict_by_hyp: Mapping[str, RedTeamVerdict],
    formal_ids: set[str],
) -> Iterable[ReviewTriggerRecord]:
    for hyp in artifact.hypotheses:
        verdict = latest_verdict_by_hyp.get(hyp.hypothesis_id)
        if verdict is None:
            continue
        if (
            verdict.decision is RedTeamDecision.material_unresolved
            and hyp.status is HypothesisStatus.verified
            and hyp.hypothesis_id in formal_ids
        ):
            yield ReviewTriggerRecord(
                trigger_name="verdict_status_conflict",
                detail=(
                    f"verdict=material_unresolved but status=verified on {hyp.hypothesis_id!r}"
                ),
                hypothesis_ids=[hyp.hypothesis_id],
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _latest_verdict_by_hypothesis(
    verdicts: list[RedTeamVerdict],
) -> dict[str, RedTeamVerdict]:
    latest: dict[str, RedTeamVerdict] = {}
    for verdict in verdicts:
        existing = latest.get(verdict.hypothesis_id)
        if existing is None or verdict.created_at > existing.created_at:
            latest[verdict.hypothesis_id] = verdict
    return latest


def _keyword_list(config: Mapping[str, object] | None, trigger_name: str) -> tuple[str, ...]:
    """Pull the trigger's keyword list from the YAML config.

    Falls back to the hard-coded defaults when the config is absent or
    doesn't specify keywords — keeps the evaluator usable in tests
    without forcing every test to load the YAML.
    """

    if trigger_name == "material_negative_allegation":
        default = _DEFAULT_NEGATIVE_ALLEGATION_KEYWORDS
    elif trigger_name == "listing_qualification_risk":
        default = _DEFAULT_LISTING_RISK_KEYWORDS
    else:
        default = ()
    if not config:
        return default
    triggers = config.get("triggers")
    if not isinstance(triggers, list):
        return default
    for entry in triggers:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("trigger_name") != trigger_name:
            continue
        when = entry.get("when")
        if not isinstance(when, Mapping):
            return default
        keywords = when.get("hypothesis_title_keywords_any")
        if isinstance(keywords, list):
            return tuple(str(k) for k in keywords)
    return default


def _title_keyword_trigger(
    formal_hypotheses: list[RiskHypothesis],
    keywords: tuple[str, ...],
    *,
    trigger_name: str,
) -> Iterable[ReviewTriggerRecord]:
    if not keywords:
        return
    matches: list[str] = []
    matched_keywords: set[str] = set()
    for hyp in formal_hypotheses:
        hits = [kw for kw in keywords if kw in hyp.title or kw in hyp.statement]
        if hits:
            matches.append(hyp.hypothesis_id)
            matched_keywords.update(hits)
    if matches:
        yield ReviewTriggerRecord(
            trigger_name=trigger_name,  # type: ignore[arg-type]
            detail=f"matched keywords: {', '.join(sorted(matched_keywords))}",
            hypothesis_ids=matches,
        )
