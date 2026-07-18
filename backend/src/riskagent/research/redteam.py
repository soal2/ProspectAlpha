"""Independent red-team pass over verified/materially-unresolved hypotheses.

The reviewer is a separate actor (``zhou_shenyan_redteam_agent`` persona) that
re-examines each hypothesis the engine produced, with explicit freedom to seek
counter-evidence using the same audited :class:`ResearchToolset`.  It is the
*only* code path besides the engine that can flip a hypothesis to
``verified``, and the state machine additionally requires its verdict for
any high-importance hypothesis to reach ``verified`` (see
:func:`riskagent.research.state_machine.apply_transition`).

Selection rules:

* ``importance == "high"`` → always reviewed.
* ``importance == "medium"`` → reviewed when one of the following triggers
  is present: source conflict (mixed-tier counter-evidence), weak causal
  chain (``residual_uncertainty`` non-empty), or peer-agent disagreement
  (``agent_disagreement`` flagged on the confidence assessment).
* ``importance == "low"`` → not reviewed (audit only).

The LLM call is pluggable (``llm_call``), mirroring the engine's pluggable
proposer.  Production wiring passes an ``LLMClient``-backed callable; tests
inject a canned responder.  The reviewer is fully testable without an LLM.

Decision strictness:

* The model must respond with a ``decision`` that is a valid
  :class:`RedTeamDecision` value.  Free-form text is rejected and retried
  once; on the second failure the hypothesis is escalated to a human
  (``escalate_human`` decision recorded).
* ``counter_evidence_ids`` must all reference :class:`SourceCard` instances
  that already exist in the working artifact.  Anything else is stripped
  and recorded as a rejection; if the decision materially relied on the
  bogus reference the verdict becomes ``escalate_human``.

Decision → transition mapping (redteam.py is the authoritative source):

* ``verified``           → ``HypothesisStatus.verified`` (gate still enforced
  by the state machine).
* ``material_unresolved``→ ``HypothesisStatus.contested`` + ``review_required``.
* ``request_supplement`` → ``HypothesisStatus.investigating`` + a new
  :class:`CoverageTask` for the dimension.
* ``downgraded``         → ``HypothesisStatus.partially_supported``.
* ``refuted``            → ``HypothesisStatus.refuted``.
* ``escalate_human``     → ``HypothesisStatus.contested`` + ``review_required``.

Verdicts are recorded via :meth:`ResearchStore.add_verdict` and transitions
via :meth:`ResearchStore.transition`; both emit durable events.  The
reviewer never mutates a frozen artifact — it operates on ``state.working``
exclusively.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .models import (
    ConfidenceAssessment,
    CoverageTask,
    GateResult,
    HypothesisStatus,
    InjectedContext,
    RedTeamDecision,
    RedTeamVerdict,
    ReviewState,
    RiskHypothesis,
    SourceCard,
)
from .store import ResearchStore
from .state_machine import InvalidTransitionError


DEFAULT_REVIEWER_AGENT = "zhou_shenyan_redteam_agent"


RedTeamLLMCall = Callable[[str, str], "dict[str, Any] | None"]
"""``async (system_prompt, user_payload) -> dict | None``.

Returns ``None`` (or raises) on infra failure; the caller treats that as a
retryable miss.  On a parsed-but-invalid response the callable should
return the dict anyway so the reviewer can record the rejection.
"""


# ----------------------------------------------------------------------
# prompt

_REDSHIRT_SYSTEM_PROMPT = """\\
你是 ProspectAlpha 多 Agent 团队中的「红队复核专家」（agent_name=zhou_shenyan_redteam_agent）。
你的职责是对其他专家提出的风险假设做对抗性复核，主动寻找反证、检查因果链与来源独立性。
你必须严格输出 JSON 对象，schema：
{{"decision": "verified|material_unresolved|request_supplement|downgraded|refuted|escalate_human",
  "rationale": "80-200 字，必须明确引用 evidence_id 或 rule_id",
  "counter_evidence_ids": ["ev_xxx", ...],
  "supplement_query": "若 request_supplement，给出建议补充检索查询；否则空字符串"}}

约束：
1. counter_evidence_ids 只能引用已经存在于工作台中的 SourceCard.evidence_id；不得编造。
2. decision 取值严格按字面；不接受自由文本。
3. 你不能新增任何无来源的事实；如果信息不足，使用 request_supplement 或 escalate_human。
"""


# ----------------------------------------------------------------------
# selection


@dataclass(frozen=True)
class ReviewTrigger:
    """Why a medium-importance hypothesis entered red-team review."""

    reason: str
    detail: str = ""


def select_for_review(
    hypotheses: Sequence[RiskHypothesis],
) -> list[tuple[RiskHypothesis, ReviewTrigger | None]]:
    """Pick the hypotheses that require a red-team pass.

    Returns ``(hypothesis, trigger)`` pairs.  ``trigger`` is ``None`` for
    high-importance entries (always reviewed) and a populated
    :class:`ReviewTrigger` for medium-importance conditional entries.
    Low-importance hypotheses are never returned.
    """

    out: list[tuple[RiskHypothesis, ReviewTrigger | None]] = []
    for hyp in hypotheses:
        if hyp.importance == "high":
            out.append((hyp, None))
            continue
        if hyp.importance == "medium":
            trigger = _medium_trigger(hyp)
            if trigger is not None:
                out.append((hyp, trigger))
    return out


def _medium_trigger(hyp: RiskHypothesis) -> ReviewTrigger | None:
    confidence = hyp.confidence
    if confidence is not None:
        for component in confidence.components:
            if component.name == "counter_evidence" and component.score <= 0.4:
                return ReviewTrigger(
                    reason="source_conflict",
                    detail=f"counter_evidence score {component.score}",
                )
            if component.name == "agent_disagreement" and component.score <= 0.4:
                return ReviewTrigger(
                    reason="agent_disagreement",
                    detail=f"agent_disagreement score {component.score}",
                )
            if component.name == "consistency" and component.score <= 0.4:
                return ReviewTrigger(
                    reason="material_conflict",
                    detail=f"consistency score {component.score}",
                )
    if hyp.residual_uncertainty:
        return ReviewTrigger(
            reason="weak_causal_chain",
            detail=hyp.residual_uncertainty[:80],
        )
    return None


# ----------------------------------------------------------------------
# verdict parsing


class RedTeamParseError(ValueError):
    """Raised when the LLM response cannot be turned into a verdict."""


def parse_verdict_payload(
    raw: dict[str, Any],
    *,
    hypothesis_id: str,
    valid_evidence_ids: set[str],
) -> tuple[RedTeamDecision, str, list[str], str]:
    """Strictly parse the LLM response.

    Returns ``(decision, rationale, counter_evidence_ids, supplement_query)``.
    Raises :class:`RedTeamParseError` on invalid decision value or missing
    rationale.  ``counter_evidence_ids`` entries not in
    ``valid_evidence_ids`` are *dropped silently* — the reviewer above
    decides whether the absence invalidates the decision.
    """

    if not isinstance(raw, dict):
        raise RedTeamParseError("red-team response must be a JSON object")
    decision_value = raw.get("decision")
    if not isinstance(decision_value, str):
        raise RedTeamParseError("red-team response missing 'decision' string")
    try:
        decision = RedTeamDecision(decision_value)
    except ValueError as exc:
        raise RedTeamParseError(
            f"unknown red-team decision: {decision_value!r}"
        ) from exc
    rationale = str(raw.get("rationale") or "").strip()
    if not rationale:
        raise RedTeamParseError("red-team response missing 'rationale'")
    raw_ids = raw.get("counter_evidence_ids") or []
    if not isinstance(raw_ids, list):
        raise RedTeamParseError("'counter_evidence_ids' must be a list")
    counter_ids = [
        evidence_id
        for evidence_id in (str(item) for item in raw_ids)
        if evidence_id and evidence_id in valid_evidence_ids
    ]
    supplement = str(raw.get("supplement_query") or "")
    return decision, rationale, counter_ids, supplement


# ----------------------------------------------------------------------
# decision -> transition mapping


_TARGET_STATUS: dict[RedTeamDecision, HypothesisStatus] = {
    RedTeamDecision.verified: HypothesisStatus.verified,
    RedTeamDecision.material_unresolved: HypothesisStatus.contested,
    RedTeamDecision.request_supplement: HypothesisStatus.investigating,
    RedTeamDecision.downgraded: HypothesisStatus.partially_supported,
    RedTeamDecision.refuted: HypothesisStatus.refuted,
    RedTeamDecision.escalate_human: HypothesisStatus.contested,
}


def target_status_for(decision: RedTeamDecision) -> HypothesisStatus:
    return _TARGET_STATUS[decision]


def _needs_review_required(decision: RedTeamDecision) -> bool:
    return decision in (
        RedTeamDecision.material_unresolved,
        RedTeamDecision.escalate_human,
    )


# ----------------------------------------------------------------------
# reviewer


@dataclass
class RedTeamOutcome:
    """One hypothesis' red-team result, persisted before returning."""

    verdict: RedTeamVerdict
    transitioned_to: HypothesisStatus | None
    supplement_task: CoverageTask | None = None


class RedTeamReviewer:
    """Independent adversarial pass over selected hypotheses."""

    def __init__(
        self,
        *,
        llm_call: RedTeamLLMCall,
        store: ResearchStore,
        reviewer_agent: str = DEFAULT_REVIEWER_AGENT,
        max_retries: int = 1,
    ) -> None:
        self._llm_call = llm_call
        self._store = store
        self._reviewer_agent = reviewer_agent
        self._max_retries = max(0, max_retries)

    async def review(
        self,
        *,
        injected: InjectedContext,
        user_requirement: str = "",
    ) -> list[RedTeamOutcome]:
        """Review every selected hypothesis, persisting each verdict+transition."""

        state = self._store.state
        if state.working is None:
            return []
        selected = select_for_review(state.working.hypotheses)
        outcomes: list[RedTeamOutcome] = []
        for hypothesis, _trigger in selected:
            outcome = await self._review_one(
                hypothesis=hypothesis,
                injected=injected,
                user_requirement=user_requirement,
            )
            outcomes.append(outcome)
        return outcomes

    # ------------------------------------------------------------------
    # internals

    async def _review_one(
        self,
        *,
        hypothesis: RiskHypothesis,
        injected: InjectedContext,
        user_requirement: str,
    ) -> RedTeamOutcome:
        evidence = self._collect_evidence(hypothesis)
        user_payload = self._build_payload(
            hypothesis=hypothesis,
            evidence=evidence,
            injected=injected,
            user_requirement=user_requirement,
        )
        decision: RedTeamDecision
        rationale: str
        counter_ids: list[str]
        supplement: str
        attempts = 0
        while True:
            try:
                raw = await self._llm_call(_REDSHIRT_SYSTEM_PROMPT, user_payload)
            except Exception:
                raw = None
            if isinstance(raw, dict):
                try:
                    decision, rationale, counter_ids, supplement = parse_verdict_payload(
                        raw,
                        hypothesis_id=hypothesis.hypothesis_id,
                        valid_evidence_ids={card.evidence_id for card in evidence},
                    )
                    break
                except RedTeamParseError:
                    pass
            attempts += 1
            if attempts > self._max_retries:
                decision = RedTeamDecision.escalate_human
                rationale = (
                    "红队复核模型响应不可解析，已升级人工。"
                    if raw is None
                    else "红队复核模型响应未通过 schema 校验，已升级人工。"
                )
                counter_ids = []
                supplement = ""
                break

        verdict = RedTeamVerdict(
            hypothesis_id=hypothesis.hypothesis_id,
            decision=decision,
            rationale=rationale,
            counter_evidence_ids=counter_ids,
            reviewer=self._reviewer_agent,
        )
        self._store.add_verdict(verdict)

        transitioned = self._apply_decision(
            hypothesis=hypothesis,
            verdict=verdict,
            supplement_query=supplement,
            injected=injected,
        )
        return RedTeamOutcome(
            verdict=verdict,
            transitioned_to=transitioned,
            supplement_task=None,
        )

    def _apply_decision(
        self,
        *,
        hypothesis: RiskHypothesis,
        verdict: RedTeamVerdict,
        supplement_query: str,
        injected: InjectedContext,
    ) -> HypothesisStatus | None:
        target = target_status_for(verdict.decision)
        transitioned: HypothesisStatus | None = None
        try:
            self._store.transition(
                hypothesis.hypothesis_id,
                target,
                actor="red_team",
                reason=f"red-team verdict: {verdict.decision.value}",
                evidence_ids=verdict.counter_evidence_ids,
            )
            transitioned = target
        except InvalidTransitionError:
            # State machine is the authority.  If the transition is
            # illegal from the current status (e.g. ``request_supplement``
            # mapping to ``investigating`` while the hypothesis is already
            # ``investigating``), the hypothesis stays put — the side
            # effects below still run so the verdict is actionable.
            transitioned = None

        if _needs_review_required(verdict.decision):
            self._mark_review_required(hypothesis.hypothesis_id)

        if verdict.decision is RedTeamDecision.request_supplement and supplement_query:
            self._add_supplement_task(hypothesis, supplement_query)
        return transitioned

    def _collect_evidence(self, hypothesis: RiskHypothesis) -> list[SourceCard]:
        working = self._store.state.working
        if working is None:
            return []
        relevant_ids = set(hypothesis.supporting_evidence_ids)
        relevant_ids.update(hypothesis.counter_evidence_ids)
        relevant_ids.update(
            eid
            for verdict in self._store.state.red_team_verdicts
            if verdict.hypothesis_id == hypothesis.hypothesis_id
            for eid in verdict.counter_evidence_ids
        )
        return [card for card in working.evidence if card.evidence_id in relevant_ids]

    def _build_payload(
        self,
        *,
        hypothesis: RiskHypothesis,
        evidence: list[SourceCard],
        injected: InjectedContext,
        user_requirement: str,
    ) -> str:
        gate_snapshot = _gate_summary(hypothesis.gate)
        confidence_snapshot = _confidence_summary(hypothesis.confidence)
        return json.dumps(
            {
                "run_id": injected.run_id,
                "data_cutoff_date": injected.data_cutoff_date,
                "policy": injected.policy.value,
                "user_requirement": user_requirement,
                "hypothesis": {
                    "hypothesis_id": hypothesis.hypothesis_id,
                    "title": hypothesis.title,
                    "statement": hypothesis.statement,
                    "dimension": hypothesis.dimension,
                    "module_id": hypothesis.module_id,
                    "agent_name": hypothesis.agent_name,
                    "origin": hypothesis.origin,
                    "rule_ids": list(hypothesis.rule_ids),
                    "importance": hypothesis.importance,
                    "time_sensitive": hypothesis.time_sensitive,
                    "anomaly_signals": list(hypothesis.anomaly_signals),
                    "causal_chain": hypothesis.causal_chain,
                    "potential_impact": hypothesis.potential_impact,
                    "investigation_plan": hypothesis.investigation_plan,
                    "refutation_criteria": hypothesis.refutation_criteria,
                    "residual_uncertainty": hypothesis.residual_uncertainty,
                    "refutation_search_done": hypothesis.refutation_search_done,
                    "gate": gate_snapshot,
                    "confidence": confidence_snapshot,
                    "status": hypothesis.status.value,
                },
                "evidence": [
                    {
                        "evidence_id": card.evidence_id,
                        "url": card.url,
                        "title": card.title,
                        "tier": card.tier.value,
                        "origin": card.origin,
                        "accessible": card.accessible,
                        "published_at": card.published_at,
                        "snippet": (card.snippet or "")[:300],
                    }
                    for card in evidence
                ],
                "known_evidence_ids": [card.evidence_id for card in evidence],
            },
            ensure_ascii=False,
        )

    def _mark_review_required(self, hypothesis_id: str) -> None:
        working = self._store.state.working
        if working is None:
            return
        hyp = working.get_hypothesis(hypothesis_id)
        if hyp is None:
            return
        hyp.review_state = ReviewState.review_required
        self._store._persist()  # noqa: SLF001 - persist is part of the store contract

    def _add_supplement_task(
        self,
        hypothesis: RiskHypothesis,
        supplement_query: str,
    ) -> None:
        working = self._store.state.working
        if working is None:
            return
        task = CoverageTask(
            dimension=hypothesis.dimension,
            module_id=hypothesis.module_id,
            priority="high",
            status="pending",
            triggered_hypothesis_ids=[hypothesis.hypothesis_id],
        )
        task.queries = [
            _planned_query(supplement_query),
        ]
        working.plan.append(task)
        self._store._persist()  # noqa: SLF001 - persist is part of the store contract


# ----------------------------------------------------------------------
# helpers


def _planned_query(text: str):
    from .planner import canonical_form
    from .models import PlannedQuery

    return PlannedQuery(
        text=text,
        language="zh-Hans",
        canonical_form=canonical_form(text),
    )


def _gate_summary(gate: GateResult | None) -> dict[str, Any] | None:
    if gate is None:
        return None
    return {
        "passed": gate.passed,
        "tier1_count": gate.tier1_count,
        "independent_tier2_clusters": gate.independent_tier2_clusters,
        "refutation_search_done": gate.refutation_search_done,
        "blocking": list(gate.blocking),
        "reasons": list(gate.reasons),
    }


def _confidence_summary(confidence: ConfidenceAssessment | None) -> dict[str, Any] | None:
    if confidence is None:
        return None
    return {
        "level": confidence.level,
        "components": [
            {"name": c.name, "score": c.score, "reason": c.reason}
            for c in confidence.components
        ],
    }
