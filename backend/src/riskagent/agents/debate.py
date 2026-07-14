"""Multi-round expert debate phase.

Sits between risk_identification and cross_validation. Each expert critiques
peers' findings (round 1), revises their own findings (round 2), then the L3
red-team arbiter (周慎言 / ``zhou_shenyan_redteam_agent``) issues an
:class:`ArbiterRuling` that decides whether the run may proceed.

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
    raw = await call_llm_with_resilience(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_payload},
        ],
        temperature=0.2,
        context={"phase": "critique", "critic": critic_agent, "target": target_agent},
    )
    raw.setdefault("critic_agent", critic_agent)
    raw.setdefault("target_agent", target_agent)
    raw.setdefault("finding_ids", [str(f.get("finding_id")) for f in target_findings if f.get("finding_id")])
    return PeerCritique.model_validate(raw).model_dump()


async def _generate_revision(
    *,
    agent_name: str,
    original_findings: list[dict[str, Any]],
    critiques_received: list[dict[str, Any]],
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
    raw = await call_llm_with_resilience(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_payload},
        ],
        temperature=0.2,
        context={"phase": "revision", "agent_name": agent_name},
    )
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
) -> DebatePhaseResult:
    """Run the 2-round peer debate and arbiter ruling.

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

    history = {
        "company_name": company_name,
        "arbiter_agent_name": arbiter_agent_name,
        "agents": agents,
        "rounds": rounds,
        "ruling": ruling.model_dump(),
        "module_result_count": len(module_results or []),
        "failures": failure_log,
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
) -> DebatePhaseResult:
    """Synchronous wrapper around :func:`run_debate_phase` for non-async callers."""

    return asyncio.run(
        run_debate_phase(
            company_name=company_name,
            findings=findings,
            module_results=module_results,
            arbiter_agent_name=arbiter_agent_name,
        )
    )
