"""Dynamic LangGraph supervisor backed by A2A JSON-mailbox expert threads."""

from __future__ import annotations

import asyncio
import inspect
import operator
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field

from riskagent.a2a import MessageType, TeamRuntime
from riskagent.a2a.storage import resolve_agent_runtime_root
from riskagent.agent_experts import AgentSpec, AgentSpecRepository
from riskagent.agents.orchestrator import (
    StrategyAssignment,
    StrategyDecision,
    manual_recovery_strategy_decision,
    manual_strategy_decision,
    orchestrate,
    select_replacement,
    validate_replacement_decision,
    validate_strategy_decision,
)
from riskagent.risk_rules import RiskRule, RiskRuleRepository
from riskagent.seed_data import AGENT_SPECS
from riskagent.skills.risk_extract import RuleEvaluation, evaluate_text_against_rules
from riskagent.skills.risk_skill_registry import (
    rules_for_skill,
    rules_from_snapshot,
    skill_for_agent,
)


MAX_REPLACEMENT_ROUNDS = 2
TEAM_STARTUP_TIMEOUT_SECONDS = 10.0
EXPERT_TASK_TIMEOUT_SECONDS = 300.0
TEAM_SHUTDOWN_TIMEOUT_SECONDS = 10.0


StrategyProvider = Callable[..., Any]
ReplacementProvider = Callable[..., Any]


class ExpertModuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module_id: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)
    module: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    status: Literal["completed"]
    rule_ids: list[str] = Field(default_factory=list)
    skill_id: str | None = None
    rules_read: int = Field(ge=0)
    limitations: list[str] = Field(default_factory=list)
    peer_message_ids: list[str] = Field(default_factory=list)
    peer_reviews: list[dict[str, Any]] = Field(default_factory=list)


class ExpertTaskOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluations: list[RuleEvaluation]
    module_result: ExpertModuleResult


@dataclass
class AgentTeamResult:
    evaluations: list[RuleEvaluation] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    agent_counts: dict[str, int] = field(default_factory=dict)
    skill_rule_counts: dict[str, int] = field(default_factory=dict)
    strategy_plan: dict[str, Any] = field(default_factory=dict)
    module_results: list[dict[str, Any]] = field(default_factory=list)
    team_state: list[dict[str, Any]] = field(default_factory=list)
    replacement_history: list[dict[str, Any]] = field(default_factory=list)
    paused: bool = False
    interrupt_reason: str | None = None


class AgentState(TypedDict, total=False):
    run_id: str
    company_name: str
    user_requirement: str
    document_summary: str
    text: str
    max_findings: int
    rule_snapshot: dict[str, Any]
    available_agents: list[dict[str, Any]]
    manual_agent_names: list[str]
    strategy_plan: dict[str, Any]
    execution_plan: dict[str, Any]
    strategy_error: str
    team_needs_manual: bool
    evaluations: list[dict[str, Any]]
    events: Annotated[list[dict[str, Any]], operator.add]
    agent_counts: dict[str, int]
    skill_rule_counts: dict[str, int]
    module_results: list[dict[str, Any]]
    team_state: list[dict[str, Any]]
    replacement_history: list[dict[str, Any]]
    excluded_agents: list[str]
    recovery_mode: bool


class AgentTeamSupervisor:
    def __init__(
        self,
        *,
        repository: RiskRuleRepository,
        agent_spec_repository: AgentSpecRepository | None = None,
        agent_specs: list[AgentSpec] | None = None,
        strategy_provider: StrategyProvider | None = None,
        replacement_provider: ReplacementProvider | None = None,
        checkpointer: Any | None = None,
        runtime_root: Path | str | None = None,
    ) -> None:
        self.repository = repository
        self.agent_spec_repository = agent_spec_repository or AgentSpecRepository()
        self._agent_specs_override = list(agent_specs) if agent_specs is not None else None
        self.strategy_provider = strategy_provider or orchestrate
        self.replacement_provider = replacement_provider or select_replacement
        self.runtime_root = resolve_agent_runtime_root(runtime_root)
        self._runtime_events: dict[str, list[dict[str, Any]]] = {}
        self._runtime_events_lock = threading.Lock()
        self._checkpointer = checkpointer
        self._graph: Any | None = None
        self._graph_lock = threading.Lock()

    @property
    def graph(self):
        if self._graph is not None:
            return self._graph
        with self._graph_lock:
            if self._graph is None:
                checkpointer = self._checkpointer or _build_checkpointer(self.runtime_root)
                self._graph = self._compile_graph(checkpointer)
        return self._graph

    def run(
        self,
        *,
        run_id: str,
        company_name: str,
        user_requirement: str,
        document_summary: str,
        text: str,
        max_findings: int,
        rule_snapshot: dict[str, Any] | None = None,
        manual_agent_names: list[str] | None = None,
    ) -> AgentTeamResult:
        initial: AgentState = {
            "run_id": run_id,
            "company_name": company_name,
            "user_requirement": user_requirement,
            "document_summary": document_summary[:4000],
            "text": text,
            "max_findings": max_findings,
            "rule_snapshot": rule_snapshot or self.repository.snapshot(),
            "available_agents": self.agent_options(),
            "manual_agent_names": manual_agent_names or [],
            "evaluations": [],
            "events": [],
            "module_results": [],
            "replacement_history": [],
        }
        config = {"configurable": {"thread_id": run_id}}
        output = self.graph.invoke(initial, config)
        return self._result_from_output(output, config)

    def resume_with_selection(self, *, run_id: str, agent_names: list[str]) -> AgentTeamResult:
        config = {"configurable": {"thread_id": run_id}}
        output = self.graph.invoke(
            Command(
                resume={"agent_names": agent_names},
                update={"available_agents": self.agent_options()},
            ),
            config,
        )
        return self._result_from_output(output, config)

    def agent_options(self) -> list[dict[str, Any]]:
        public_base_url = os.getenv("A2A_PUBLIC_BASE_URL", "http://localhost:8000")
        return [
            {
                **spec.capability_payload(public_base_url=public_base_url),
                "available": True,
            }
            for spec in self._current_agent_specs()
        ]

    def _current_agent_specs(self) -> list[AgentSpec]:
        """Load the current expert catalog for every plan or re-plan."""

        if self._agent_specs_override is not None:
            return list(self._agent_specs_override)
        if os.getenv("USE_DATABASE", "1") == "0":
            return _seed_agent_specs()
        return self.agent_spec_repository.list_specs()

    def _compile_graph(self, checkpointer: Any):
        graph = StateGraph(AgentState)
        graph.add_node("strategize", self._strategy_node)
        graph.add_node("manual_selection", self._manual_selection_node)
        graph.add_node("run_dynamic_team", self._run_dynamic_team_node)
        graph.add_node("finish", self._finish_node)
        graph.add_edge(START, "strategize")
        graph.add_conditional_edges(
            "strategize",
            lambda state: "manual_selection" if state.get("strategy_error") else "run_dynamic_team",
            ["manual_selection", "run_dynamic_team"],
        )
        graph.add_edge("manual_selection", "run_dynamic_team")
        graph.add_conditional_edges(
            "run_dynamic_team",
            lambda state: "manual_selection" if state.get("team_needs_manual") else "finish",
            ["manual_selection", "finish"],
        )
        graph.add_edge("finish", END)
        return graph.compile(checkpointer=checkpointer)

    def _strategy_node(self, state: AgentState) -> AgentState:
        manual_names = state.get("manual_agent_names", [])
        if manual_names:
            try:
                plan = manual_strategy_decision(
                    agent_names=manual_names,
                    available_agents=state["available_agents"],
                )
            except Exception as exc:
                return {"strategy_error": str(exc)}
        else:
            try:
                plan = _resolve(
                    self.strategy_provider(
                        user_requirement=state["user_requirement"],
                        company_name=state["company_name"],
                        document_summary=state["document_summary"],
                        available_agents=state["available_agents"],
                    )
                )
                plan = validate_strategy_decision(plan, state["available_agents"])
            except Exception as exc:
                return {
                    "strategy_error": str(exc),
                    "events": [
                        _event(
                            "strategy_validation_failed",
                            "orchestrator",
                            "战略规划未通过格式校验",
                            str(exc)[:400],
                        )
                    ],
                }
        return {
            "strategy_plan": plan.model_dump(),
            "execution_plan": {},
            "strategy_error": "",
            "team_needs_manual": False,
            "events": [
                _event(
                    "strategy_decision_validated",
                    "orchestrator",
                    "战略层完成动态专家选择",
                    f"选择 {len(plan.selected_agents)} 位专家并生成独立模块任务。",
                    payload=plan.model_dump(),
                )
            ],
        }

    def _manual_selection_node(self, state: AgentState) -> AgentState:
        reason = state.get("strategy_error") or "动态替补耗尽，需要人工选择专家。"
        selection = interrupt(
            {
                "reason": reason,
                "available_agents": state.get("available_agents", []),
                "failed_modules": [
                    item
                    for item in state.get("replacement_history", [])
                    if item.get("status") == "failed"
                ],
            }
        )
        names = selection.get("agent_names", []) if isinstance(selection, dict) else []
        failed_assignments: list[StrategyAssignment] = []
        if state.get("strategy_plan"):
            previous_plan = StrategyDecision.model_validate(state["strategy_plan"])
            completed_modules = {
                str(item.get("module_id"))
                for item in state.get("module_results", [])
                if item.get("module_id")
            }
            failed_assignments = [
                assignment
                for assignment in previous_plan.assignments
                if assignment.module_id not in completed_modules
            ]
        if failed_assignments:
            plan = manual_recovery_strategy_decision(
                agent_names=names,
                failed_assignments=failed_assignments,
                available_agents=state.get("available_agents", []),
            )
        else:
            plan = manual_strategy_decision(
                agent_names=names,
                available_agents=state.get("available_agents", []),
            )
        excluded_agents = {
            str(item.get("agent_name"))
            for item in state.get("team_state", [])
            if item.get("agent_name")
        }
        excluded_agents.update(
            str(item.get(key))
            for item in state.get("replacement_history", [])
            for key in ("failed_agent", "replacement_agent")
            if item.get(key)
        )
        return {
            **(
                {"execution_plan": plan.model_dump()}
                if failed_assignments
                else {"strategy_plan": plan.model_dump(), "execution_plan": {}}
            ),
            "strategy_error": "",
            "team_needs_manual": False,
            "excluded_agents": sorted(excluded_agents),
            "recovery_mode": bool(failed_assignments),
            "events": [
                _event(
                    "manual_strategy_resumed",
                    "user",
                    "人工选择已恢复动态协作图",
                    f"选择 {len(plan.selected_agents)} 位专家，任务由 Agent Card 自动生成。",
                    payload=plan.model_dump(),
                )
            ],
        }

    def _run_dynamic_team_node(self, state: AgentState) -> AgentState:
        plan = StrategyDecision.model_validate(
            state.get("execution_plan") or state["strategy_plan"]
        )
        specs = {spec.name: spec for spec in self._current_agent_specs()}
        all_rules = rules_from_snapshot(state["rule_snapshot"])
        events: list[dict[str, Any]] = []
        with self._runtime_events_lock:
            self._runtime_events[state["run_id"]] = []
        runtime = TeamRuntime(
            state["run_id"],
            root_dir=self.runtime_root,
            event_callback=lambda event: self._record_runtime_event(state["run_id"], event),
        )
        attempted = set(plan.selected_agents) | set(state.get("excluded_agents", []))
        assignment_by_task: dict[str, StrategyAssignment] = {}
        result_payloads: dict[str, dict[str, Any]] = {}
        failures: list[tuple[StrategyAssignment, str]] = []
        try:
            started_assignments: list[StrategyAssignment] = []
            for assignment in plan.assignments:
                try:
                    spec = specs[assignment.agent_name]
                    capability = spec.capability_payload()
                    handler = _validated_expert_handler(
                        self._expert_handler(
                            spec=spec,
                            assignment=assignment,
                            text=state["text"],
                            rules=all_rules,
                            rule_snapshot=state["rule_snapshot"],
                        ),
                        assignment,
                    )
                    runtime.start_expert(
                        assignment.agent_name,
                        handler,
                        role=str(capability.get("role") or spec.title),
                        capabilities=capability.get("capabilities", []),
                        input_modes=capability.get("input_modes", []),
                        output_modes=capability.get("output_modes", []),
                    )
                    started_assignments.append(assignment)
                except Exception as exc:
                    failures.append((assignment, f"expert startup failed: {exc}"))
            idle_assignments: list[StrategyAssignment] = []
            for assignment in started_assignments:
                try:
                    if not runtime.wait_until_idle(
                        [assignment.agent_name], TEAM_STARTUP_TIMEOUT_SECONDS
                    ):
                        raise TimeoutError("expert did not reach idle state")
                    idle_assignments.append(assignment)
                except Exception as exc:
                    failures.append((assignment, f"expert initialization failed: {exc}"))
            for assignment in idle_assignments:
                task_id = _runtime_task_id(state["run_id"], assignment.module_id)
                try:
                    runtime.assign_task(
                        assignment.agent_name,
                        task_id,
                        {"assignment": assignment.model_dump()},
                    )
                    assignment_by_task[task_id] = assignment
                except Exception as exc:
                    failures.append((assignment, f"task dispatch failed: {exc}"))
            for task_id, assignment in assignment_by_task.items():
                try:
                    item = runtime.collect_results(
                        [task_id],
                        EXPERT_TASK_TIMEOUT_SECONDS,
                    ).get(task_id)
                except TimeoutError:
                    runtime.fail_task(task_id, "expert result timeout")
                    item = None
                if not item:
                    failures.append((assignment, "expert result timeout"))
                elif item.get("status") != "completed":
                    failures.append((assignment, str(item.get("error") or "expert failed")))
                else:
                    try:
                        result_payloads[assignment.module_id] = _validate_expert_output(
                            item.get("result"),
                            assignment,
                        )
                    except Exception as exc:
                        validation_error = f"invalid expert output: {exc}"
                        runtime.fail_task(task_id, validation_error)
                        failures.append((assignment, validation_error))

            replacement_history: list[dict[str, Any]] = []
            unresolved: list[tuple[StrategyAssignment, str]] = []
            for failed_assignment, initial_reason in failures:
                reason = initial_reason
                resolved = False
                for round_number in range(1, MAX_REPLACEMENT_ROUNDS + 1):
                    live_specs = {
                        spec.name: spec for spec in self._current_agent_specs()
                    }
                    candidates = [
                        item
                        for item in self.agent_options()
                        if item.get("agent_name") not in attempted
                        and item.get("agent_name") in live_specs
                    ]
                    if not candidates:
                        break
                    try:
                        decision = validate_replacement_decision(
                            _resolve(
                                self.replacement_provider(
                                    company_name=state["company_name"],
                                    failed_assignment=failed_assignment,
                                    failure_reason=reason,
                                    available_candidates=candidates,
                                )
                            ),
                            failed_assignment=failed_assignment,
                            available_candidates=candidates,
                        )
                        replacement_name = str(decision.agent_name)
                        attempted.add(replacement_name)
                        replacement_assignment = failed_assignment.model_copy(
                            update={"agent_name": replacement_name}
                        )
                        attempt_task_id = _runtime_task_id(
                            state["run_id"],
                            failed_assignment.module_id,
                            attempt=round_number,
                        )
                        replacement_spec = live_specs[replacement_name]
                        replacement_capability = (
                            replacement_spec.capability_payload()
                        )
                        replacement_handler = _validated_expert_handler(
                            self._expert_handler(
                                spec=replacement_spec,
                                assignment=replacement_assignment,
                                text=state["text"],
                                rules=all_rules,
                                rule_snapshot=state["rule_snapshot"],
                            ),
                            replacement_assignment,
                        )
                        runtime.start_expert(
                            replacement_name,
                            replacement_handler,
                            role=str(
                                replacement_capability.get("role")
                                or replacement_spec.title
                            ),
                            capabilities=replacement_capability.get("capabilities", []),
                            input_modes=replacement_capability.get("input_modes", []),
                            output_modes=replacement_capability.get("output_modes", []),
                        )
                        if not runtime.wait_until_idle(
                            [replacement_name], TEAM_STARTUP_TIMEOUT_SECONDS
                        ):
                            raise TimeoutError("replacement expert did not reach idle")
                        runtime.assign_task(
                            replacement_name,
                            attempt_task_id,
                            {"assignment": replacement_assignment.model_dump()},
                        )
                        try:
                            replacement_result = runtime.collect_results(
                                [attempt_task_id], EXPERT_TASK_TIMEOUT_SECONDS
                            ).get(attempt_task_id, {})
                        except TimeoutError:
                            runtime.fail_task(
                                attempt_task_id,
                                "replacement expert result timeout",
                            )
                            replacement_result = {
                                "status": "failed",
                                "error": "replacement expert result timeout",
                            }
                        record = {
                            "module_id": failed_assignment.module_id,
                            "failed_agent": failed_assignment.agent_name,
                            "replacement_agent": replacement_name,
                            "round": round_number,
                            "reason": reason,
                            "status": replacement_result.get("status", "failed"),
                        }
                        replacement_history.append(record)
                        if replacement_result.get("status") == "completed":
                            try:
                                result_payloads[failed_assignment.module_id] = (
                                    _validate_expert_output(
                                        replacement_result.get("result"),
                                        replacement_assignment,
                                    )
                                )
                                resolved = True
                                break
                            except Exception as exc:
                                reason = f"invalid replacement output: {exc}"
                                runtime.fail_task(attempt_task_id, reason)
                                record["status"] = "failed"
                                record["reason"] = reason
                                continue
                        reason = str(replacement_result.get("error") or "replacement failed")
                    except Exception as exc:
                        reason = str(exc)
                        replacement_history.append(
                            {
                                "module_id": failed_assignment.module_id,
                                "failed_agent": failed_assignment.agent_name,
                                "round": round_number,
                                "reason": reason,
                                "status": "failed",
                            }
                        )
                if not resolved:
                    unresolved.append((failed_assignment, reason))

            evaluations: list[dict[str, Any]] = []
            module_results: list[dict[str, Any]] = []
            agent_counts: dict[str, int] = {}
            skill_rule_counts: dict[str, int] = {}
            team_state: list[dict[str, Any]] = []
            for module_id, payload in result_payloads.items():
                serialized = list(payload.get("evaluations") or [])
                evaluations.extend(serialized)
                result = dict(payload.get("module_result") or {})
                result.setdefault("module_id", module_id)
                module_results.append(result)
                agent_name = str(result.get("agent_name") or "unknown")
                agent_counts[agent_name] = sum(
                    1 for item in serialized if item.get("evaluation_result") == "triggered"
                )
                if result.get("skill_id"):
                    skill_rule_counts[str(result["skill_id"])] = int(
                        result.get("rules_read") or 0
                    )
                team_state.append(
                    {
                        "agent_name": agent_name,
                        "status": "completed",
                        "current_module": module_id,
                        "replacement_round": next(
                            (
                                int(item["round"])
                                for item in reversed(replacement_history)
                                if item.get("replacement_agent") == agent_name
                            ),
                            0,
                        ),
                    }
                )
            for assignment, reason in unresolved:
                team_state.append(
                    {
                        "agent_name": assignment.agent_name,
                        "status": "failed",
                        "current_module": assignment.module_id,
                        "replacement_round": MAX_REPLACEMENT_ROUNDS,
                        "error": reason,
                    }
                )
                replacement_history.append(
                    {
                        "module_id": assignment.module_id,
                        "failed_agent": assignment.agent_name,
                        "reason": reason,
                        "status": "failed",
                    }
                )
            try:
                runtime.shutdown_all(TEAM_SHUTDOWN_TIMEOUT_SECONDS)
            except Exception as exc:
                self._record_runtime_event(
                    state["run_id"],
                    {"type": "shutdown_failed", "error": str(exc)},
                )
            events.extend(self._take_runtime_events(state["run_id"]))
            events.append(
                _event(
                    "dynamic_team_completed" if not unresolved else "dynamic_team_paused",
                    "orchestrator",
                    "动态专家协作完成" if not unresolved else "动态替补耗尽",
                    (
                        f"收齐 {len(module_results)} 个专家模块结果。"
                        if not unresolved
                        else f"仍有 {len(unresolved)} 个模块缺少合法结果，等待人工选择。"
                    ),
                )
            )
            existing_evaluations = list(state.get("evaluations", []))
            existing_modules = list(state.get("module_results", []))
            existing_history = list(state.get("replacement_history", []))
            existing_team_state = list(state.get("team_state", []))
            if state.get("recovery_mode"):
                replacement_history.extend(
                    {
                        "module_id": item.get("module_id"),
                        "replacement_agent": item.get("agent_name"),
                        "reason": "manual expert recovery",
                        "status": "completed",
                        "manual": True,
                    }
                    for item in module_results
                )
            merged_team_state = {
                str(item.get("current_module") or item.get("agent_name")): item
                for item in existing_team_state
            }
            merged_team_state.update(
                {
                    str(item.get("current_module") or item.get("agent_name")): item
                    for item in team_state
                }
            )
            return {
                "evaluations": [*existing_evaluations, *evaluations],
                "events": events,
                "agent_counts": agent_counts,
                "skill_rule_counts": skill_rule_counts,
                "module_results": [*existing_modules, *module_results],
                "team_state": list(merged_team_state.values()),
                "replacement_history": [*existing_history, *replacement_history],
                "team_needs_manual": bool(unresolved),
                "recovery_mode": False,
                "execution_plan": {},
                "strategy_error": (
                    "动态替补两轮后仍有专家模块失败。" if unresolved else ""
                ),
            }
        except Exception as exc:
            try:
                runtime.shutdown_all(TEAM_SHUTDOWN_TIMEOUT_SECONDS)
            except Exception as shutdown_exc:
                self._record_runtime_event(
                    state["run_id"],
                    {"type": "shutdown_failed", "error": str(shutdown_exc)},
                )
            events.extend(self._take_runtime_events(state["run_id"]))
            return {
                "events": [
                    *events,
                    _event(
                        "dynamic_team_paused",
                        "orchestrator",
                        "专家线程启动或通信失败",
                        str(exc)[:400],
                    ),
                ],
                "team_needs_manual": True,
                "strategy_error": str(exc),
            }
        finally:
            try:
                runtime.shutdown_all(TEAM_SHUTDOWN_TIMEOUT_SECONDS)
            except Exception as exc:
                self._record_runtime_event(
                    state["run_id"],
                    {"type": "shutdown_failed", "error": str(exc)},
                )

    def _expert_handler(
        self,
        *,
        spec: AgentSpec,
        assignment: StrategyAssignment,
        text: str,
        rules: list[RiskRule],
        rule_snapshot: dict[str, Any],
    ):
        def handle(payload: dict[str, Any], peer_context: Any) -> dict[str, Any]:
            if peer_context.message_type == MessageType.TASK:
                if not isinstance(payload, dict) or "assignment" not in payload:
                    raise ValueError("Expert task payload must contain a strategy assignment")
                received_assignment = StrategyAssignment.model_validate(
                    payload["assignment"]
                )
                if received_assignment != assignment:
                    raise ValueError(
                        "Expert task assignment does not match the strategist-bound module"
                    )
            skill = skill_for_agent(spec.name)
            selected_rules = (
                rules_for_skill(skill, rules, set()) if skill else _rules_for_spec(spec, rules)
            )
            evaluations = evaluate_text_against_rules(
                text,
                rules=selected_rules,
                repository=self.repository,
                rule_set_snapshot=rule_snapshot,
            )
            for item in evaluations:
                item.source_type = spec.name
                item.skill_id = skill.skill_id if skill else None
            triggered = [item for item in evaluations if item.evaluation_result == "triggered"]
            if peer_context.message_type == MessageType.PEER_REQUEST:
                return {
                    "reviewer_agent": spec.name,
                    "reviewed_module": payload.get("origin_module"),
                    "related_rule_ids": [item.rule_id for item in triggered[:10]],
                    "summary": (
                        f"{spec.title} 通过 A2A 对等消息复核，"
                        f"在自身能力域发现 {len(triggered)} 条可能相关规则命中。"
                    ),
                }

            peer_message_ids: list[str] = []
            peer_reviews: list[dict[str, Any]] = []
            peer_review_error: str | None = None
            selected_peer = _select_peer_for_review(
                peer_context.available_peers,
                assignment=assignment,
                evaluations=triggered,
            )
            if selected_peer:
                try:
                    request_id = peer_context.send(
                        selected_peer,
                        {
                            "kind": "cross_module_review",
                            "origin_agent": spec.name,
                            "origin_module": assignment.module_id,
                            "finding_summaries": [
                                {
                                    "rule_id": item.rule_id,
                                    "category": item.category,
                                    "reasoning_summary": item.reasoning_summary,
                                }
                                for item in triggered[:10]
                            ],
                        },
                    )
                    peer_message_ids.append(request_id)
                    review = peer_context.read(request_id, timeout=30.0)
                    if isinstance(review, dict):
                        peer_reviews.append(review)
                except Exception as exc:
                    peer_review_error = f"{type(exc).__name__}: {exc}"
            return {
                "evaluations": [item.model_dump() for item in evaluations],
                "module_result": {
                    "module_id": assignment.module_id,
                    "agent_name": spec.name,
                    "module": assignment.module,
                    "summary": (
                        f"完成 {assignment.module} 分析，读取 {len(selected_rules)} 条规则，"
                        f"形成 {len(triggered)} 条候选风险判断。"
                    ),
                    "status": "completed",
                    "rule_ids": [item.rule_id for item in triggered],
                    "skill_id": skill.skill_id if skill else None,
                    "rules_read": len(selected_rules),
                    "limitations": (
                        ([f"A2A 对等复核未完成：{peer_review_error}"] if peer_review_error else [])
                        + (
                            []
                            if triggered
                            else ["本轮未命中高置信规则，仍建议人工复核相关招股书章节。"]
                        )
                    ),
                    "peer_message_ids": peer_message_ids,
                    "peer_reviews": peer_reviews,
                },
            }

        return handle

    def _finish_node(self, state: AgentState) -> AgentState:
        evaluations = [RuleEvaluation.model_validate(item) for item in state.get("evaluations", [])]
        return {
            "evaluations": [
                item.model_dump()
                for item in _dedupe_evaluations(evaluations)
            ],
            "events": [
                _event(
                    "agent_completed",
                    "orchestrator",
                    "Agent 团队分析完成",
                    f"收齐 {len(state.get('module_results', []))} 个模块结果。",
                )
            ],
        }

    def _result_from_output(
        self,
        output: dict[str, Any],
        config: dict[str, Any],
    ) -> AgentTeamResult:
        paused = "__interrupt__" in output
        values = self.graph.get_state(config).values if paused else output
        interrupt_reason = values.get("strategy_error") or None
        if paused and output.get("__interrupt__"):
            interrupt_reason = str(output["__interrupt__"][0].value.get("reason") or interrupt_reason)
        return AgentTeamResult(
            evaluations=[
                RuleEvaluation.model_validate(item) for item in values.get("evaluations", [])
            ],
            events=list(values.get("events", [])),
            agent_counts=dict(values.get("agent_counts", {})),
            skill_rule_counts=dict(values.get("skill_rule_counts", {})),
            strategy_plan=dict(values.get("strategy_plan", {})),
            module_results=list(values.get("module_results", [])),
            team_state=list(values.get("team_state", [])),
            replacement_history=list(values.get("replacement_history", [])),
            paused=paused,
            interrupt_reason=interrupt_reason,
        )

    def _record_runtime_event(self, run_id: str, event: Any) -> None:
        if isinstance(event, dict):
            payload = dict(event)
        else:
            payload = {"type": "runtime_event", "value": str(event)}
        task_id = str(payload.get("task_id") or "")
        task_parts = task_id.split("::", 3)
        if len(task_parts) == 4 and task_parts[0] == run_id:
            payload.setdefault("module_id", task_parts[1])
        with self._runtime_events_lock:
            self._runtime_events.setdefault(run_id, []).append(payload)

    def _take_runtime_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._runtime_events_lock:
            raw = self._runtime_events.pop(run_id, [])
        events = []
        for item in raw:
            event_type = str(item.get("event_type") or item.get("type") or "a2a_runtime_event")
            actor = str(item.get("actor_name") or item.get("agent_name") or "a2a_runtime")
            events.append(
                _event(
                    event_type,
                    actor,
                    str(item.get("title") or event_type.replace("_", " ")),
                    str(item.get("summary") or item.get("error") or "A2A runtime state changed."),
                    payload=item,
                )
            )
        return events


def _resolve(value: Any) -> Any:
    return asyncio.run(value) if inspect.isawaitable(value) else value


def _runtime_task_id(run_id: str, module_id: str, *, attempt: int = 0) -> str:
    """Return an opaque A2A Task.id that remains unique across analysis runs."""

    return f"{run_id}::{module_id}::{attempt}::{uuid4().hex}"


def _build_checkpointer(runtime_root: Path):
    backend = os.getenv("LANGGRAPH_CHECKPOINT_BACKEND", "").strip().lower()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not backend:
        backend = "postgres" if database_url.startswith("postgres") else "sqlite"
    if backend == "postgres":
        if not database_url:
            raise RuntimeError("DATABASE_URL is required for the Postgres LangGraph checkpointer.")
        from psycopg import Connection
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres import PostgresSaver

        conninfo = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        connection = Connection.connect(
            conninfo,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        saver = PostgresSaver(connection)
        saver.setup()
        return saver
    if backend != "sqlite":
        raise ValueError("LANGGRAPH_CHECKPOINT_BACKEND must be sqlite or postgres.")
    from langgraph.checkpoint.sqlite import SqliteSaver

    runtime_root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        runtime_root / "langgraph_checkpoints.sqlite",
        check_same_thread=False,
    )
    return SqliteSaver(connection)


def _rules_for_spec(spec: AgentSpec, all_rules: list[RiskRule]) -> list[RiskRule]:
    selected = []
    for rule in all_rules:
        if spec.rule_types and rule.rule_type in spec.rule_types:
            selected.append(rule)
            continue
        if spec.categories and rule.category in spec.categories:
            selected.append(rule)
    return selected or (all_rules if spec.default else [])


def _validate_expert_output(
    value: Any,
    assignment: StrategyAssignment,
) -> dict[str, Any]:
    output = ExpertTaskOutput.model_validate(value)
    module = output.module_result
    if module.module_id != assignment.module_id:
        raise ValueError("module_id does not match the assigned module")
    if module.agent_name != assignment.agent_name:
        raise ValueError("agent_name does not match the assigned expert")
    if any(item.source_type != assignment.agent_name for item in output.evaluations):
        raise ValueError("evaluation source_type does not match the assigned expert")
    triggered_rule_ids = {
        item.rule_id
        for item in output.evaluations
        if item.evaluation_result == "triggered"
    }
    if set(module.rule_ids) != triggered_rule_ids:
        raise ValueError("module rule_ids do not match triggered evaluations")
    return output.model_dump()


def _validated_expert_handler(handler: Any, assignment: StrategyAssignment):
    """Validate expert output before the A2A runtime chooses a terminal Task state."""

    def guarded(payload: Any, peer_context: Any):
        value = handler(payload, peer_context)
        if peer_context.message_type != MessageType.TASK:
            return value
        if inspect.isawaitable(value):
            async def validate_async():
                return _validate_expert_output(await value, assignment)

            return validate_async()
        return _validate_expert_output(value, assignment)

    return guarded


def _select_peer_for_review(
    peers: list[dict[str, Any]],
    *,
    assignment: StrategyAssignment,
    evaluations: list[RuleEvaluation],
) -> str | None:
    if not peers:
        return None
    terms = {
        value.lower()
        for value in [
            assignment.module,
            *[item.category for item in evaluations],
            *[term for item in evaluations for term in item.matched_terms],
        ]
        if value
    }

    def score(card: dict[str, Any]) -> tuple[int, str]:
        searchable = str(card).lower()
        return (sum(term in searchable for term in terms), str(card.get("name") or ""))

    selected = max(peers, key=score)
    return str(selected.get("name") or "") or None


def _seed_agent_specs() -> list[AgentSpec]:
    return [
        AgentSpec(
            name=str(item["agent_name"]),
            title=str(item["title"]),
            rule_types=tuple(item.get("rule_types") or []),
            categories=tuple(item.get("categories") or []),
            default=bool(item.get("is_default")),
        )
        for item in AGENT_SPECS
    ]


def _dedupe_evaluations(evaluations: list[RuleEvaluation]) -> list[RuleEvaluation]:
    seen: set[str] = set()
    deduped = []
    for item in evaluations:
        if item.rule_id in seen:
            continue
        seen.add(item.rule_id)
        deduped.append(item)
    return deduped


def _event(
    event_type: str,
    actor_name: str,
    title: str,
    summary: str,
    **extra: Any,
) -> dict[str, Any]:
    from datetime import datetime, timezone

    event = {
        "event_type": event_type,
        "actor_name": actor_name,
        "title": title,
        "summary": summary,
        "stage_key": "expert_orchestration",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    event.update({key: value for key, value in extra.items() if value is not None})
    return event
