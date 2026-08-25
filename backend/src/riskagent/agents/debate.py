"""Multi-round expert debate phase.

Sits between risk_identification and cross_validation. Each expert critiques
peers' findings (round 1), revises their own findings (round 2), then the L3
red-team arbiter (周慎言 / ``zhou_shenyan_redteam_agent``) issues an
:class:`ArbiterRuling` that decides whether the run may proceed.

When the arbiter rules ``request_supplement`` with flagged findings, an
optional round 3 (focused debate) is triggered. Only the experts involved in
the contested findings participate in a targeted exchange — no full
cross-qualification re-run.

Resilience:
    Every LLM call goes through
    :func:`riskagent.agents.llm_resilience.call_llm_with_resilience`, which adds
    retry with exponential backoff for transient failures and trips a
    process-wide circuit breaker on repeated outages.
    Round-level partial failures (some critiques/reversions fail) are tolerated
    via ``asyncio.gather(..., return_exceptions=True)``; only total failure of a
    phase raises :class:`~riskagent.agents.exceptions.DebateLLMError`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

from riskagent.agents.exceptions import DebateLLMError
from riskagent.agents.llm_resilience import (
    CircuitBreakerOpen,
    call_llm_with_resilience,
)
from riskagent.agents.orchestrator import (
    ArbiterRuling,
    FindingRevision,
    PeerCritique,
    arbitrate_debate,
)
from riskagent.providers.llm import LLMError


logger = logging.getLogger(__name__)


CRITIQUE_SYSTEM_PROMPT = """\
你是 ProspectAlpha 多 Agent 团队中的「{critic_agent}」专家，正在第二轮立论阶段对同侪 finding 进行质证。
你必须基于招股书原文证据、风险规则触发条件与立论逻辑，给出严谨、可追溯的质证意见；不得无依据否定，
也不得为质证而质证。只输出 JSON：
{{"critic_agent":"{critic_agent}","target_agent":str,"finding_ids":[str],
"critique_summary":str,"suggested_revisions":[str],"severity":"info"|"minor"|"major"|"blocker"}}
"""


REVISION_SYSTEM_PROMPT = """\
你是 ProspectAlpha 多 Agent 团队中的「{agent_name}」专家。第二轮立论要求你综合同侪质证意见，对自身 finding 进行修订。
保留有证据支撑的判断，调整被合理质疑的部分，并明示新增或移除的证据与 finding。只输出 JSON：
{{"agent_name":"{agent_name}","original_finding_ids":[str],"revised_summary":str,
"added_evidence_ids":[str],"removed_finding_ids":[str],"revision_reason":str}}
"""


FOCUSED_DEBATE_CRITIQUE_PROMPT = """\
你是 ProspectAlpha 多 Agent 团队中的「{critic_agent}」专家。仲裁官已标记以下 finding 存在争议，要求你进行第三轮聚焦质证。
你只需针对被标记的争议 finding 发表意见，不得扩大质证范围。基于招股书原文证据与风险规则，给出严谨的质证意见。只输出 JSON：
{{"critic_agent":"{critic_agent}","target_agent":str,"finding_ids":[str],
"critique_summary":str,"suggested_revisions":[str],"severity":"info"|"minor"|"major"|"blocker"}}
"""


FOCUSED_DEBATE_RESPONSE_PROMPT = """\
你是 ProspectAlpha 多 Agent 团队中的「{agent_name}」专家。第三轮聚焦辩论中，仲裁官标记了你的 finding 存在争议。
请针对聚焦质证意见做出回应，补充证据或调整立论。只输出 JSON：
{{"agent_name":"{agent_name}","original_finding_ids":[str],"revised_summary":str,
"added_evidence_ids":[str],"removed_finding_ids":[str],"revision_reason":str}}
"""


@dataclass
class DebateRoundResult:
    critiques: list[dict[str, Any]] = field(default_factory=list)
    revisions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DebatePhaseResult:
    critiques: list[dict[str, Any]]
    revisions: list[dict[str, Any]]
    ruling: ArbiterRuling
    history: dict[str, Any]
    partial_failure_count: int = 0

    @property
    def decision(self) -> str:
        return self.ruling.decision


def _finding_index(findings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("finding_id")): item for item in findings if item.get("finding_id")}


def _findings_by_agent(findings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in findings:
        agent = str(item.get("agent_name") or "")
        if not agent:
            continue
        grouped.setdefault(agent, []).append(item)
    return grouped


def _failure_record(critic_or_agent: str, target: str | None, exc: BaseException) -> dict[str, Any]:
    return {
        "actor": critic_or_agent,
        "target": target,
        "error_class": type(exc).__name__,
        "error_message": str(exc),
    }


async def _generate_critique(
    *,
    critic_agent: str,
    target_agent: str,
    target_findings: list[dict[str, Any]],
    live_call: Any = None,
) -> dict[str, Any]:
    system = CRITIQUE_SYSTEM_PROMPT.format(critic_agent=critic_agent)
    user_payload = json.dumps(
        {
            "critic_agent": critic_agent,
            "target_agent": target_agent,
            "target_findings": target_findings,
        },
        ensure_ascii=False,
    )
    try:
        raw = await call_llm_with_resilience(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_payload}],
            temperature=0.2,
            context={"phase": "critique", "critic": critic_agent, "target": target_agent},
            on_delta=live_call.delta if live_call else None,
            on_response=live_call.complete if live_call else None,
        )
    except Exception as exc:
        if live_call:
            live_call.fail(exc)
        raise
    raw.setdefault("critic_agent", critic_agent)
    raw.setdefault("target_agent", target_agent)
    raw.setdefault("finding_ids", [str(f.get("finding_id")) for f in target_findings if f.get("finding_id")])
    return PeerCritique.model_validate(raw).model_dump()


async def _generate_focused_critique(
    *,
    critic_agent: str,
    target_agent: str,
    contested_findings: list[dict[str, Any]],
    live_call: Any = None,
) -> dict[str, Any]:
    """Round 3: targeted critique on arbiter-flagged contested findings only."""
    system = FOCUSED_DEBATE_CRITIQUE_PROMPT.format(critic_agent=critic_agent)
    user_payload = json.dumps(
        {
            "critic_agent": critic_agent,
            "target_agent": target_agent,
            "contested_findings": contested_findings,
            "context": "focused_debate_round",
        },
        ensure_ascii=False,
    )
    try:
        raw = await call_llm_with_resilience(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_payload}],
            temperature=0.2,
            context={"phase": "focused_debate_critique", "critic": critic_agent, "target": target_agent},
            on_delta=live_call.delta if live_call else None,
            on_response=live_call.complete if live_call else None,
        )
    except Exception as exc:
        if live_call:
            live_call.fail(exc)
        raise
    raw.setdefault("critic_agent", critic_agent)
    raw.setdefault("target_agent", target_agent)
    raw.setdefault("finding_ids", [str(f.get("finding_id")) for f in contested_findings if f.get("finding_id")])
    return PeerCritique.model_validate(raw).model_dump()


async def _generate_focused_response(
    *,
    agent_name: str,
    contested_findings: list[dict[str, Any]],
    focused_critiques: list[dict[str, Any]],
    live_call: Any = None,
) -> dict[str, Any]:
    """Round 3: targeted response to focused critique on contested findings."""
    system = FOCUSED_DEBATE_RESPONSE_PROMPT.format(agent_name=agent_name)
    user_payload = json.dumps(
        {
            "agent_name": agent_name,
            "contested_findings": contested_findings,
            "focused_critiques_received": focused_critiques,
            "context": "focused_debate_response",
        },
        ensure_ascii=False,
    )
    try:
        raw = await call_llm_with_resilience(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_payload}],
            temperature=0.2,
            context={"phase": "focused_debate_response", "agent_name": agent_name},
            on_delta=live_call.delta if live_call else None,
            on_response=live_call.complete if live_call else None,
        )
    except Exception as exc:
        if live_call:
            live_call.fail(exc)
        raise
    raw.setdefault("agent_name", agent_name)
    raw.setdefault("original_finding_ids", [str(f.get("finding_id")) for f in contested_findings if f.get("finding_id")])
    return FindingRevision.model_validate(raw).model_dump()


async def _generate_revision(
    *,
    agent_name: str,
    original_findings: list[dict[str, Any]],
    critiques_received: list[dict[str, Any]],
    live_call: Any = None,
) -> dict[str, Any]:
    system = REVISION_SYSTEM_PROMPT.format(agent_name=agent_name)
    user_payload = json.dumps(
        {
            "agent_name": agent_name,
            "original_findings": original_findings,
            "critiques_received": critiques_received,
        },
        ensure_ascii=False,
    )
    try:
        raw = await call_llm_with_resilience(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_payload}],
            temperature=0.2,
            context={"phase": "revision", "agent_name": agent_name},
            on_delta=live_call.delta if live_call else None,
            on_response=live_call.complete if live_call else None,
        )
    except Exception as exc:
        if live_call:
            live_call.fail(exc)
        raise
    raw.setdefault("agent_name", agent_name)
    raw.setdefault("original_finding_ids", [str(f.get("finding_id")) for f in original_findings if f.get("finding_id")])
    return FindingRevision.model_validate(raw).model_dump()


def _split_gather_results(
    results: list[Any],
    pairs: list[tuple[str, str | None]],
    phase: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Exception]]:
    """Split ``gather(return_exceptions=True)`` output into successes/failures.

    Each failure is logged at WARNING level so the chain is observable.
    """

    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    raw_failures: list[Exception] = []
    for (actor, target), result in zip(pairs, results):
        if isinstance(result, Exception):
            failures.append(_failure_record(actor, target, result))
            raw_failures.append(result)
            logger.warning(
                f"{phase}_partial_failure",
                extra={
                    "phase": phase,
                    "actor": actor,
                    "target": target,
                    "error_class": type(result).__name__,
                    "error": str(result),
                },
            )
        else:
            successes.append(result)
    return successes, failures, raw_failures


async def run_debate_phase(
    *,
    company_name: str,
    findings: list[dict[str, Any]],
    module_results: list[dict[str, Any]] | None = None,
    arbiter_agent_name: str = "zhou_shenyan_redteam_agent",
    live_call_factory: Callable[[str], Any] | None = None,
) -> DebatePhaseResult:
    """Run the multi-round peer debate and arbiter ruling.

    The debate has up to 3 rounds:
      - Round 1: each agent critiques every other agent's findings.
      - Round 2: each agent revises their findings based on critiques received.
      - Arbiter ruling by L3 red-team agent.
      - Round 3 (optional): focused debate on contested findings only, triggered
        when the arbiter rules ``request_supplement`` with flagged finding IDs.

    Partial-failure tolerance: if some critiques or revisions fail but at least
    one succeeds, the debate continues and the failed items are recorded in
    ``history["failures"]`` and counted in ``partial_failure_count``. Only when
    *every* item in a phase fails (or the arbiter call fails) is
    :class:`~riskagent.agents.exceptions.DebateLLMError` raised.

    Args:
        company_name: Subject company.
        findings: List of finding dicts (must include finding_id, agent_name).
        module_results: Optional module_results list for context.
        arbiter_agent_name: L3 red-team persona agent_name.

    Returns:
        DebatePhaseResult with critiques, revisions, ruling, and full history.

    Raises:
        DebateLLMError: When all critiques, all revisions, or the arbiter call fail.
    """

    findings_by_agent = _findings_by_agent(findings)
    agents = list(findings_by_agent.keys())
    rounds: list[dict[str, Any]] = []
    failure_log: dict[str, list[dict[str, Any]]] = {"critique": [], "revision": []}
    partial_failure_count = 0

    # Round 1: each agent critiques every other agent's findings.
    round1_tasks = []
    pairs: list[tuple[str, str | None]] = []
    for critic in agents:
        for target in agents:
            if critic == target:
                continue
            round1_tasks.append(
            _generate_critique(
                    critic_agent=critic,
                    target_agent=target,
                target_findings=findings_by_agent[target],
                live_call=live_call_factory(critic) if live_call_factory else None,
                )
            )
            pairs.append((critic, target))
    round1_raw = await asyncio.gather(*round1_tasks, return_exceptions=True) if round1_tasks else []
    round1_critiques, crit_failures, crit_exc = _split_gather_results(round1_raw, pairs, "critique")
    rounds.append({"round": 1, "kind": "critique", "items": round1_critiques})
    failure_log["critique"].extend(crit_failures)
    partial_failure_count += len(crit_failures)
    if round1_tasks and not round1_critiques:
        first = crit_exc[0] if crit_exc else None
        raise DebateLLMError(
            phase="critique",
            failed=len(crit_failures),
            total=len(round1_tasks),
            detail=str(first) if first else "all critiques failed",
        )

    # Round 2: each agent revises their findings based on critiques received.
    critiques_by_target: dict[str, list[dict[str, Any]]] = {}
    for critique in round1_critiques:
        critiques_by_target.setdefault(str(critique["target_agent"]), []).append(critique)
    round2_tasks = []
    round2_pairs: list[tuple[str, str | None]] = []
    for agent in agents:
        round2_tasks.append(
            _generate_revision(
                agent_name=agent,
                original_findings=findings_by_agent[agent],
                critiques_received=critiques_by_target.get(agent, []),
                live_call=live_call_factory(agent) if live_call_factory else None,
            )
        )
        round2_pairs.append((agent, None))
    round2_raw = await asyncio.gather(*round2_tasks, return_exceptions=True) if round2_tasks else []
    round2_revisions, rev_failures, rev_exc = _split_gather_results(round2_raw, round2_pairs, "revision")
    rounds.append({"round": 2, "kind": "revision", "items": round2_revisions})
    failure_log["revision"].extend(rev_failures)
    partial_failure_count += len(rev_failures)
    if round2_tasks and not round2_revisions:
        first = rev_exc[0] if rev_exc else None
        raise DebateLLMError(
            phase="revision",
            failed=len(rev_failures),
            total=len(round2_tasks),
            detail=str(first) if first else "all revisions failed",
        )

    findings_with_critiques = [
        {
            "finding": finding,
            "critiques": [
                critique
                for critique in round1_critiques
                if str(finding.get("finding_id")) in critique.get("finding_ids", [])
            ],
            "revision": next(
                (
                    revision
                    for revision in round2_revisions
                    if str(finding.get("finding_id")) in revision.get("original_finding_ids", [])
                    or revision.get("agent_name") == finding.get("agent_name")
                ),
                None,
            ),
        }
        for finding in findings
    ]

    try:
        ruling = await arbitrate_debate(
            company_name=company_name,
            findings_with_critiques=findings_with_critiques,
            arbiter_agent_name=arbiter_agent_name,
            live_call=live_call_factory(arbiter_agent_name) if live_call_factory else None,
        )
    except (LLMError, CircuitBreakerOpen) as exc:
        logger.warning(
            "debate_arbiter_failed",
            extra={"phase": "arbitration", "error_class": type(exc).__name__, "error": str(exc)},
        )
        raise DebateLLMError(
            phase="arbitration",
            failed=1,
            total=1,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    # --- Round 3: Focused debate on contested findings (if arbiter requested supplement) ---
    focused_critiques: list[dict[str, Any]] = []
    focused_responses: list[dict[str, Any]] = []
    needs_focused_debate = ruling.decision == "request_supplement" and bool(ruling.flagged_finding_ids)

    if needs_focused_debate:
        contested_finding_ids = set(ruling.flagged_finding_ids)
        logger.info(
            "debate_round3_triggered",
            extra={
                "phase": "focused_debate",
                "contested_finding_ids": sorted(contested_finding_ids),
                "reason": "arbiter_request_supplement",
            },
        )

        # Build index of contested findings and identify involved agents
        finding_index = _finding_index(findings)
        contested_findings_by_agent: dict[str, list[dict[str, Any]]] = {}
        for fid in contested_finding_ids:
            finding = finding_index.get(fid)
            if finding:
                agent_name = str(finding.get("agent_name") or "")
                if agent_name:
                    contested_findings_by_agent.setdefault(agent_name, []).append(finding)

        # Round 3a: focused critiques — each non-flagged agent critiques contested findings
        flagged_agents = set(contested_findings_by_agent.keys())
        critiquing_agents = [a for a in agents if a not in flagged_agents]
        round3a_tasks = []
        round3a_pairs: list[tuple[str, str | None]] = []
        for critic in critiquing_agents:
            for target_agent, target_contested in contested_findings_by_agent.items():
                round3a_tasks.append(
                    _generate_focused_critique(
                        critic_agent=critic,
                        target_agent=target_agent,
                        contested_findings=target_contested,
                        live_call=live_call_factory(critic) if live_call_factory else None,
                    )
                )
                round3a_pairs.append((critic, target_agent))

        round3a_raw = await asyncio.gather(*round3a_tasks, return_exceptions=True) if round3a_tasks else []
        round3a_successes, r3a_failures, r3a_exc = _split_gather_results(
            round3a_raw, round3a_pairs, "focused_debate_critique"
        )
        focused_critiques = round3a_successes
        failure_log.setdefault("focused_debate_critique", []).extend(r3a_failures)
        partial_failure_count += len(r3a_failures)

        rounds.append({
            "round": 3,
            "round_type": "focused_debate",
            "kind": "focused_critique",
            "contested_finding_ids": sorted(contested_finding_ids),
            "items": focused_critiques,
        })

        # Round 3b: focused responses — flagged agents respond to focused critiques
        critiques_by_target: dict[str, list[dict[str, Any]]] = {}
        for critique in focused_critiques:
            critiques_by_target.setdefault(str(critique["target_agent"]), []).append(critique)

        round3b_tasks = []
        round3b_pairs: list[tuple[str, str | None]] = []
        for target_agent, target_contested in contested_findings_by_agent.items():
            round3b_tasks.append(
                _generate_focused_response(
                    agent_name=target_agent,
                    contested_findings=target_contested,
                    focused_critiques=critiques_by_target.get(target_agent, []),
                    live_call=live_call_factory(target_agent) if live_call_factory else None,
                )
            )
            round3b_pairs.append((target_agent, None))

        round3b_raw = await asyncio.gather(*round3b_tasks, return_exceptions=True) if round3b_tasks else []
        round3b_successes, r3b_failures, r3b_exc = _split_gather_results(
            round3b_raw, round3b_pairs, "focused_debate_response"
        )
        focused_responses = round3b_successes
        failure_log.setdefault("focused_debate_response", []).extend(r3b_failures)
        partial_failure_count += len(r3b_failures)

        rounds.append({
            "round": 3,
            "round_type": "focused_debate",
            "kind": "focused_response",
            "contested_finding_ids": sorted(contested_finding_ids),
            "items": focused_responses,
        })

        logger.info(
            "debate_round3_completed",
            extra={
                "phase": "focused_debate",
                "focused_critiques": len(focused_critiques),
                "focused_responses": len(focused_responses),
            },
        )
    else:
        logger.info(
            "debate_round3_skipped",
            extra={
                "phase": "focused_debate",
                "reason": "no_request_supplement_or_no_flagged_findings",
                "arbiter_decision": ruling.decision,
            },
        )

    history = {
        "company_name": company_name,
        "arbiter_agent_name": arbiter_agent_name,
        "agents": agents,
        "rounds": rounds,
        "ruling": ruling.model_dump(),
        "module_result_count": len(module_results or []),
        "failures": failure_log,
        "focused_debate_triggered": needs_focused_debate,
    }
    return DebatePhaseResult(
        critiques=round1_critiques,
        revisions=round2_revisions,
        ruling=ruling,
        history=history,
        partial_failure_count=partial_failure_count,
    )


def run_debate_phase_sync(
    *,
    company_name: str,
    findings: list[dict[str, Any]],
    module_results: list[dict[str, Any]] | None = None,
    arbiter_agent_name: str = "zhou_shenyan_redteam_agent",
    live_call_factory: Callable[[str], Any] | None = None,
) -> DebatePhaseResult:
    """Synchronous wrapper around :func:`run_debate_phase` for non-async callers."""

    return asyncio.run(
        run_debate_phase(
            company_name=company_name,
            findings=findings,
            module_results=module_results,
            arbiter_agent_name=arbiter_agent_name,
            live_call_factory=live_call_factory,
        )
    )
