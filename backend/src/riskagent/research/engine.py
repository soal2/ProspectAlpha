"""Per-module hypothesis investigation engine.

The engine drives a bounded tool-loop per proposed hypothesis: it runs the
operations the proposer planned (search → fetch → refutation search), collects
the resulting :class:`SourceCard` instances from the shared ledger, evaluates
the verification gate and explainable confidence, then applies the state-
machine transition through the durable :class:`ResearchStore`.

The proposer is a plain callable injected at construction time so the engine
is fully testable without an LLM.  A production wiring will pass an
``LLMClient``-backed proposer; the engine itself never imports the LLM stack.
``run_module`` returns ``None`` when there is nothing to investigate (no
coverage tasks and no anomaly signals) so callers can skip research cleanly
in ``quick`` mode.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Sequence

from . import state_machine
from .gate import VerificationGate
from .confidence import ConfidenceCalculator
from .gateway import GatewayError
from .models import (
    ConfidenceAssessment,
    CoverageTask,
    ExternalSearchPolicy,
    HypothesisStatus,
    HypothesisTransition,
    InjectedContext,
    RiskHypothesis,
    SourceCard,
    ToolAuditRecord,
)
from .planner import AssignmentHint
from .store import ResearchStore
from .tools import (
    TOOL_BOCHA_WEB_SEARCH,
    TOOL_FETCH_WEB_SOURCE,
    ResearchToolset,
    ToolContext,
)


@dataclass(frozen=True)
class ProposerContext:
    """Read-only bundle handed to a :data:`HypothesisProposer`."""

    module_id: str
    agent_name: str
    company_name: str
    user_requirement: str
    assignment_goal: str
    coverage_tasks: Sequence[CoverageTask]
    rule_candidates: Sequence[Any]
    screening_observations: Sequence[str]
    pdf_context: str
    injected: InjectedContext


@dataclass
class SearchOperation:
    """Planned ``bocha_web_search`` invocation."""

    query: str
    purpose: str
    coverage_task_id: str | None = None
    refutation: bool = False
    freshness: str | None = None
    count: int | None = None
    include_domains: list[str] | None = None
    exclude_domains: list[str] | None = None


@dataclass
class FetchOperation:
    """Planned ``fetch_web_source`` invocation."""

    url: str
    purpose: str
    refutation: bool = False


Operation = SearchOperation | FetchOperation


@dataclass
class HypothesisProposal:
    """A hypothesis plus the ordered ops the engine should run for it."""

    hypothesis: RiskHypothesis
    operations: list[Operation] = field(default_factory=list)


HypothesisProposer = Callable[
    [ProposerContext], list[HypothesisProposal] | Awaitable[list[HypothesisProposal]]
]


@dataclass(frozen=True)
class ExpertResearchContext:
    """Per-module bundle the supervisor hands to ``_expert_handler``.

    Encapsulates everything ``HypothesisEngine.run_module`` needs that the
    supervisor already has at module-bound time: the engine (with its durable
    ``ResearchStore``), the immutable server-side :class:`InjectedContext`,
    this module's slice of the baseline plan, and the company/user context.
    The supervisor builds one of these per assignment via a factory so the
    engine loop itself stays agnostic of the strategy/assignment shapes.
    """

    engine: "HypothesisEngine"
    injected: InjectedContext
    coverage_tasks: Sequence[CoverageTask]
    company_name: str
    user_requirement: str
    assignment: AssignmentHint
    pdf_context: str = ""


ResearchFactory = Callable[[str, str], ExpertResearchContext | None]
"""``(module_id, module_name) -> ExpertResearchContext | None``.

The factory receives both the module id and the human-readable module name
so it can resolve which baseline dimension the assignment maps to without
needing to import orchestrator types.  ``None`` means research is disabled
for this module (quick mode with no anomalies, no Bocha key, or module not
mappable to any baseline dimension).  Returning ``None`` is the explicit
skip path — the engine's own ``run_module`` would also return ``None`` when
there is nothing to investigate, but the factory lets us short-circuit
without paying the import cost.
"""


@dataclass
class ModuleResearchPayload:
    """What ``run_module`` hands back to the supervisor / Hook B."""

    hypotheses: list[RiskHypothesis]
    evidence: list[SourceCard]
    audit: list[ToolAuditRecord]
    transitions: list[HypothesisTransition]
    coverage_task_ids_done: list[str]
    external_incomplete: bool = False
    failure_reason: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "hypotheses": [h.model_dump(mode="json") for h in self.hypotheses],
            "evidence": [c.model_dump(mode="json") for c in self.evidence],
            "audit": [a.model_dump(mode="json") for a in self.audit],
            "transitions": [t.model_dump(mode="json") for t in self.transitions],
            "coverage_task_ids_done": list(self.coverage_task_ids_done),
            "external_incomplete": self.external_incomplete,
            "failure_reason": self.failure_reason,
        }


class HypothesisEngine:
    def __init__(
        self,
        *,
        proposer: HypothesisProposer,
        toolset_factory: Callable[[ToolContext], ResearchToolset],
        store: ResearchStore,
        gate: VerificationGate | None = None,
        confidence: ConfidenceCalculator | None = None,
        max_operations_per_hypothesis: int = 8,
    ) -> None:
        self._proposer = proposer
        self._toolset_factory = toolset_factory
        self._store = store
        self._gate = gate or VerificationGate()
        self._confidence = confidence or ConfidenceCalculator()
        self._max_ops = max_operations_per_hypothesis

    async def run_module(
        self,
        *,
        module_id: str,
        agent_name: str,
        company_name: str,
        user_requirement: str = "",
        assignment: AssignmentHint | None = None,
        coverage_tasks: Sequence[CoverageTask] = (),
        rule_candidates: Sequence[Any] = (),
        screening_observations: Sequence[str] = (),
        pdf_context: str = "",
        injected: InjectedContext | None = None,
    ) -> ModuleResearchPayload | None:
        if injected is None:
            return None
        if not coverage_tasks and not screening_observations:
            return None

        ctx = ProposerContext(
            module_id=module_id,
            agent_name=agent_name,
            company_name=company_name,
            user_requirement=user_requirement,
            assignment_goal=assignment.goal if assignment else "",
            coverage_tasks=tuple(coverage_tasks),
            rule_candidates=tuple(rule_candidates),
            screening_observations=tuple(screening_observations),
            pdf_context=pdf_context,
            injected=injected,
        )
        proposed = self._proposer(ctx)
        proposals = await proposed if inspect.isawaitable(proposed) else proposed
        if not proposals:
            return None

        tool_context = self._build_tool_context(
            module_id, agent_name, injected, proposals
        )
        toolset = self._toolset_factory(tool_context)

        done_task_ids: list[str] = []
        external_incomplete = False
        failure_reason: str | None = None

        for proposal in proposals:
            hypothesis = proposal.hypothesis
            hypothesis.module_id = module_id
            hypothesis.agent_name = agent_name
            self._store.add_hypothesis(hypothesis)
            self._safe_transition(
                hypothesis.hypothesis_id,
                HypothesisStatus.investigating,
                actor="engine",
                reason="investigation started",
            )

            refutation_done, ops_failure = await self._run_operations(
                toolset, hypothesis, proposal.operations
            )
            if ops_failure is not None:
                external_incomplete = True
                failure_reason = ops_failure
                self._safe_transition(
                    hypothesis.hypothesis_id,
                    HypothesisStatus.inconclusive,
                    actor="system",
                    reason=ops_failure,
                )
                continue

            hypothesis.refutation_search_done = refutation_done
            self._finalise_hypothesis(hypothesis, toolset, injected)
            for op in proposal.operations:
                if isinstance(op, SearchOperation) and op.coverage_task_id:
                    if op.coverage_task_id not in done_task_ids:
                        done_task_ids.append(op.coverage_task_id)

        working = self._store.state.working
        hypotheses = list(working.hypotheses) if working else []
        evidence = list(working.evidence) if working else []
        return ModuleResearchPayload(
            hypotheses=hypotheses,
            evidence=evidence,
            audit=list(toolset.ledger.audit),
            transitions=list(self._store.state.transitions),
            coverage_task_ids_done=done_task_ids,
            external_incomplete=external_incomplete,
            failure_reason=failure_reason,
        )

    # ------------------------------------------------------------------
    # internals

    async def _run_operations(
        self,
        toolset: ResearchToolset,
        hypothesis: RiskHypothesis,
        operations: Sequence[Operation],
    ) -> tuple[bool, str | None]:
        """Execute the planned operations in order.

        Returns ``(refutation_done, failure_reason)``.  ``failure_reason`` is
        set when a transient :class:`GatewayError` aborts the loop — the
        hypothesis is left for the caller to mark inconclusive.
        """
        refutation_done = False
        ops = list(operations)[: self._max_ops]
        for op in ops:
            purpose = op.purpose
            if op.refutation:
                purpose = f"反证: {purpose}"
                refutation_done = True
            try:
                if isinstance(op, SearchOperation):
                    # Tool requires hypothesis_id XOR coverage_task_id.  The
                    # engine is hypothesis-driven, so we bind to the hypothesis
                    # and let ``coverage_task_id`` flow only through the
                    # ``done_task_ids`` bookkeeping above.
                    args: dict[str, Any] = {
                        "query": op.query,
                        "purpose": purpose,
                        "hypothesis_id": hypothesis.hypothesis_id,
                    }
                    if op.freshness:
                        args["freshness"] = op.freshness
                    if op.count is not None:
                        args["count"] = op.count
                    if op.include_domains:
                        args["include_domains"] = op.include_domains
                    if op.exclude_domains:
                        args["exclude_domains"] = op.exclude_domains
                    await toolset.dispatch(TOOL_BOCHA_WEB_SEARCH, args)
                elif isinstance(op, FetchOperation):
                    await toolset.dispatch(
                        TOOL_FETCH_WEB_SOURCE,
                        {
                            "url": op.url,
                            "purpose": purpose,
                            "hypothesis_id": hypothesis.hypothesis_id,
                        },
                    )
            except GatewayError as exc:
                if exc.transient:
                    return refutation_done, f"transient gateway error: {exc.kind}"
                return refutation_done, f"persistent gateway error: {exc.kind}"
        return refutation_done, None

    def _finalise_hypothesis(
        self,
        hypothesis: RiskHypothesis,
        toolset: ResearchToolset,
        injected: InjectedContext,
    ) -> None:
        evidence_for_hyp = [
            card
            for card in toolset.ledger.evidence
            if card.hypothesis_id == hypothesis.hypothesis_id
        ]
        hypothesis.supporting_evidence_ids = [c.evidence_id for c in evidence_for_hyp]
        self._store.add_evidence(evidence_for_hyp)

        gate_result = self._gate.evaluate(
            hypothesis,
            evidence_for_hyp,
            data_cutoff_date=injected.data_cutoff_date,
        )
        hypothesis.gate = gate_result
        hypothesis.confidence = self._confidence.assess(
            hypothesis,
            evidence_for_hyp,
            gate_result,
            data_cutoff_date=injected.data_cutoff_date,
        )

        target = self._decide_status(gate_result, evidence_for_hyp, hypothesis)
        self._safe_transition(
            hypothesis.hypothesis_id,
            target,
            actor="engine",
            reason=f"gate passed={gate_result.passed}, blocking={gate_result.blocking}",
            evidence_ids=hypothesis.supporting_evidence_ids,
            gate_snapshot=gate_result,
        )

    @staticmethod
    def _decide_status(
        gate_result: Any,
        evidence: Sequence[SourceCard],
        hypothesis: RiskHypothesis,
    ) -> HypothesisStatus:
        # Engine never authorises ``verified`` directly: the state machine
        # requires ``investigating -> partially_supported -> verified`` and
        # high-importance verified additionally requires a red-team verdict.
        # The engine's job is to land the hypothesis at the strongest status
        # the evidence supports *under the gate*; promotion to ``verified``
        # is the red-team reviewer's call.
        if gate_result.blocking:
            return HypothesisStatus.contested
        if gate_result.passed:
            return HypothesisStatus.partially_supported
        if any(c.accessible and c.origin == "fetched_page" for c in evidence):
            return HypothesisStatus.partially_supported
        return HypothesisStatus.inconclusive

    def _safe_transition(
        self,
        hypothesis_id: str,
        to_status: HypothesisStatus,
        *,
        actor: str,
        reason: str,
        evidence_ids: Sequence[str] = (),
        gate_snapshot: Any = None,
    ) -> None:
        try:
            self._store.transition(
                hypothesis_id,
                to_status,
                actor=actor,
                reason=reason,
                evidence_ids=evidence_ids,
                gate_snapshot=gate_snapshot,
            )
        except state_machine.InvalidTransitionError:
            # The state machine is the authority — if it rejects (e.g. high
            # importance needs a red-team verdict), the hypothesis stays in
            # its current status and the engine moves on.
            return

    def _build_tool_context(
        self,
        module_id: str,
        agent_name: str,
        injected: InjectedContext,
        proposals: list[HypothesisProposal],
    ) -> ToolContext:
        hyp_ids = {p.hypothesis.hypothesis_id for p in proposals}
        task_ids = {
            op.coverage_task_id
            for p in proposals
            for op in p.operations
            if isinstance(op, SearchOperation) and op.coverage_task_id
        }

        return ToolContext(
            run_id=injected.run_id,
            data_cutoff_date=injected.data_cutoff_date,
            policy=injected.policy,
            module_id=module_id,
            agent_name=agent_name,
            valid_hypothesis_ids=lambda: set(hyp_ids),
            valid_task_ids=lambda: set(task_ids),
        )


def run_engine_async(coro):
    """Run an engine coroutine from synchronous test code."""
    return asyncio.run(coro)
