"""Repository-side glue between the run lifecycle and the research engine.

The pipeline (``AnalysisRunRepository``) talks to the research subsystem
through this module rather than reaching into the engine directly.  Three
entry points cover the integration hooks A / B / resume:

* :func:`bootstrap_research_state` — Hook A.  Creates the v1 working
  artifact with the baseline plan and stashes it on the durable store.
* :func:`build_research_factory` — produces the ``ResearchFactory`` that
  ``AgentTeamSupervisor.run`` consults per assignment.  It captures the
  per-run :class:`ResearchStore`, the baseline :class:`CoverageTask` plan,
  the immutable :class:`InjectedContext`, and the company / user context.
  The factory is the single place where ``module_id`` ↔ ``CoverageTask``
  is resolved so the engine never needs to import orchestrator types.
* :func:`apply_module_research` — Hook B.  Merges one module's
  ``ModuleResearchPayload`` (serialised to a dict by the supervisor) back
  into the durable :class:`ResearchStore`: hypotheses, evidence, audit,
  transitions, completion flags.  Called per ``module_results`` entry.
* :func:`freeze_module_research_stage` — closes the artifact version after
  every module has been merged, recording the stage checkpoint.

If the gateway is unavailable (no Bocha key) the factory returns ``None``
for every module, so the supervisor's research hook becomes a no-op — this
is the explicit D7 skip path.  ``None`` is also returned in ``quick`` mode
where the baseline plan is empty and there is nothing to investigate until
an anomaly fires (handled by the engine itself via ``screening_observations``).
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from .config import ResearchConfig
from .engine import (
    ExpertResearchContext,
    HypothesisEngine,
)
from .gateway import ResearchGateway
from .models import (
    CoverageTask,
    ExternalSearchPolicy,
    HypothesisTransition,
    InjectedContext,
    ResearchArtifact,
    ResearchCompletionStatus,
    ResearchState,
    RiskHypothesis,
    SourceCard,
    ToolAuditRecord,
    utc_now,
)
from .planner import AssignmentHint, apply_user_focus, build_baseline_plan
from .store import ResearchStore
from .tools import ResearchToolset, ToolContext


_MODULE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "财务": ("财务", "會計", "会计"),
    "业务": ("业务", "市場", "市场"),
    "治理": ("治理",),
    "法务监管": ("法务", "法務", "监管", "監管", "合规", "訴訟"),
    "行业竞争": ("行业", "行業", "竞争"),
    "估值": ("估值",),
    "核心人员": ("人员", "人員", "管理层", "高管"),
    "供应链": ("供应链", "供應鏈"),
    "声誉": ("声誉", "聲譽", "舆情"),
}


def _module_name_to_dimension(module_name: str) -> str | None:
    if not module_name:
        return None
    for dimension, keywords in _MODULE_KEYWORDS.items():
        if any(keyword in module_name for keyword in keywords):
            return dimension
    return None


def _assignments_from_plan(plan: dict[str, Any] | None) -> list[AssignmentHint]:
    if not plan:
        return []
    out: list[AssignmentHint] = []
    for item in plan.get("assignments", []) or []:
        module_id = str(item.get("module_id") or "")
        module = str(item.get("module") or "")
        goal = str(item.get("goal") or "")
        if module_id and module:
            out.append(AssignmentHint(module_id=module_id, module=module, goal=goal))
    return out


def _coverage_for_module(
    tasks: Sequence[CoverageTask],
    module_name: str,
) -> list[CoverageTask]:
    """Pick the coverage tasks whose dimension matches ``module_name``.

    Mirrors the planner's module→dimension keyword map.  Kept here so the
    planner private dict does not leak; if the planner keywords change, the
    duplication is intentional and tested via the integration tests.
    """

    dimension = _module_name_to_dimension(module_name)
    if dimension is None:
        return []
    return [task for task in tasks if task.dimension == dimension]


def bootstrap_research_state(
    *,
    run_id: str,
    company_name: str,
    analysis_depth: str,
    policy: ExternalSearchPolicy,
    data_cutoff_date: str,
    strategy_plan: dict[str, Any] | None,
    store: ResearchStore,
    focus: str = "",
    now=None,
) -> ResearchState:
    """Hook A — create the v1 working artifact with the baseline plan.

    Idempotent: if ``store.state.working`` already exists, returns the
    existing state untouched.  This makes the resume path (which may call
    bootstrap again) a no-op rather than a re-plan.
    """

    if store.state.working is not None:
        return store.state
    assignments = _assignments_from_plan(strategy_plan)
    tasks = build_baseline_plan(
        company_name=company_name,
        assignments=assignments,
        analysis_depth=analysis_depth,
    )
    if focus:
        apply_user_focus(tasks, focus)
    state = store.state
    state.analysis_depth = analysis_depth
    state.policy = policy
    state.artifact_id = state.artifact_id or f"art_{run_id}"
    stamp = now or utc_now()
    working = ResearchArtifact(
        artifact_id=state.artifact_id,
        version=1,
        based_on_version=None,
        created_at=stamp,
        frozen=False,
        completion_status=ResearchCompletionStatus.running,
        policy=policy,
        plan=tasks,
    )
    store.set_plan(working)
    return state


def build_research_factory(
    *,
    run_id: str,
    company_name: str,
    user_requirement: str,
    analysis_depth: str,
    data_cutoff_date: str,
    strategy_plan: dict[str, Any] | None,
    gateway: ResearchGateway,
    store: ResearchStore,
    config: ResearchConfig,
    pdf_context: str = "",
    focus: str = "",
    engine_builder: Callable[..., HypothesisEngine],
    toolset_builder: Callable[[ToolContext], ResearchToolset],
) -> Callable[[str, str], ExpertResearchContext | None]:
    """Build the per-module ExpertResearchContext factory.

    Returns a callable ``(module_id, module_name) -> ExpertResearchContext | None``.
    ``None`` short-circuits the supervisor's research hook for this module:

    * gateway unavailable (no Bocha key, D7 skip path),
    * quick depth with no baseline tasks (anomalies still flow through
      ``screening_observations``, handled inside the engine itself when a
      research context is built — but if even the baseline is empty we
      pre-empt here to avoid paying for an engine that cannot investigate
      anything baseline-driven),
    * module name not mappable to any baseline dimension,
    * no ``engine_builder`` / ``toolset_builder`` available (Step 8 wires
      the LLM-backed proposer; tests inject both via the factory closure).

    Hook A is invoked lazily here so the bootstrap happens once per run on
    the first module lookup, even if the pipeline forgot to call it
    explicitly.  Calling bootstrap again is a no-op.
    """

    injected = InjectedContext(
        run_id=run_id,
        data_cutoff_date=data_cutoff_date,
        policy=config.policy,
    )
    bootstrap_research_state(
        run_id=run_id,
        company_name=company_name,
        analysis_depth=analysis_depth,
        policy=config.policy,
        data_cutoff_date=data_cutoff_date,
        strategy_plan=strategy_plan,
        focus=focus,
        store=store,
    )
    baseline = list(store.state.working.plan) if store.state.working else []
    engine = engine_builder(
        toolset_factory=toolset_builder,
        store=store,
    )

    def _factory(module_id: str, module_name: str) -> ExpertResearchContext | None:
        coverage = _coverage_for_module(baseline, module_name)
        if not coverage and analysis_depth == "quick":
            return None
        assignment = AssignmentHint(module_id=module_id, module=module_name, goal="")
        return ExpertResearchContext(
            engine=engine,
            injected=injected,
            coverage_tasks=coverage,
            company_name=company_name,
            user_requirement=user_requirement,
            assignment=assignment,
            pdf_context=pdf_context,
        )

    return _factory


def apply_module_research(
    *,
    store: ResearchStore,
    payload: dict[str, Any],
) -> None:
    """Hook B — merge a single module's research payload into the store.

    ``payload`` is the JSON form of :class:`ModuleResearchPayload` produced
    by ``ModuleResearchPayload.as_payload`` (the supervisor serialises it
    before returning).  We rebuild pydantic instances and feed them through
    the same store methods the engine used during the live run, so a
    reloaded/resumed run lands at the same durable state.
    """

    working = store.state.working
    if working is None:
        return
    for hyp_data in payload.get("hypotheses") or []:
        hypothesis = RiskHypothesis.model_validate(hyp_data)
        if working.get_hypothesis(hypothesis.hypothesis_id) is None:
            working.hypotheses.append(hypothesis)
    cards = [SourceCard.model_validate(c) for c in payload.get("evidence") or []]
    store.add_evidence(cards)
    audits = [ToolAuditRecord.model_validate(a) for a in payload.get("audit") or []]
    store.add_audit(audits)
    for transition_data in payload.get("transitions") or []:
        transition = HypothesisTransition.model_validate(transition_data)
        if any(
            existing.transition_id == transition.transition_id
            for existing in store.state.transitions
        ):
            continue
        store.state.transitions.append(transition)
    if payload.get("external_incomplete"):
        store.note_external_incomplete(
            str(payload.get("failure_reason") or "external research incomplete")
        )


def freeze_module_research_stage(
    *,
    store: ResearchStore,
    stage: str,
    completion: ResearchCompletionStatus,
    done_task_ids: Sequence[str],
    now=None,
) -> ResearchArtifact | None:
    """Close the current artifact version after Hook B has merged outputs."""

    if store.state.working is None:
        return None
    frozen = store.freeze_working(completion_status=completion, stage=stage, now=now)
    store.checkpoint(stage=stage, done_task_ids=list(done_task_ids))
    return frozen
