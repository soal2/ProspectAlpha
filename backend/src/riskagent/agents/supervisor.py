"""Agent team supervisor for fixed HK-IPO risk analysis DAG."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from riskagent.agent_experts import AgentSpec, AgentSpecRepository
from riskagent.risk_rules import RiskRule, RiskRuleRepository
from riskagent.seed_data import AGENT_SPECS
from riskagent.skills.risk_extract import RuleEvaluation, evaluate_text_against_rules
from riskagent.skills.risk_skill_registry import (
    DomainSkill,
    rules_for_skill,
    rules_from_snapshot,
    skill_for_agent,
    skill_payload,
)


@dataclass
class AgentTeamResult:
    evaluations: list[RuleEvaluation]
    events: list[dict[str, Any]]
    agent_counts: dict[str, int]
    skill_rule_counts: dict[str, int]


class AgentState(TypedDict, total=False):
    text: str
    max_findings: int
    rule_snapshot: dict[str, Any]
    all_rules: list[RiskRule]
    assigned_rule_ids: set[str]
    evaluations: list[RuleEvaluation]
    events: list[dict[str, Any]]
    agent_counts: dict[str, int]
    skill_rule_counts: dict[str, int]


class AgentTeamSupervisor:
    def __init__(
        self,
        *,
        repository: RiskRuleRepository,
        agent_spec_repository: AgentSpecRepository | None = None,
        agent_specs: list[AgentSpec] | None = None,
    ) -> None:
        self.repository = repository
        self.agent_spec_repository = agent_spec_repository or AgentSpecRepository()
        self.agent_specs = agent_specs or (
            _seed_agent_specs()
            if os.getenv("USE_DATABASE", "1") == "0"
            else self.agent_spec_repository.list_specs()
        )
        self.graph = self._compile_graph()

    def run(
        self,
        *,
        text: str,
        max_findings: int,
        selected_agent_names: list[str] | None = None,
        rule_snapshot: dict[str, Any] | None = None,
    ) -> AgentTeamResult:
        specs = self._selected_specs(selected_agent_names)
        graph = self.graph if len(specs) == len(self.agent_specs) else self._compile_graph(specs)
        initial_state: AgentState = {"text": text, "max_findings": max_findings}
        if rule_snapshot:
            initial_state["rule_snapshot"] = rule_snapshot
        final_state = graph.invoke(initial_state)
        return AgentTeamResult(
            evaluations=_dedupe_evaluations(final_state.get("evaluations", []))[: max_findings * 4],
            events=final_state.get("events", []),
            agent_counts=final_state.get("agent_counts", {}),
            skill_rule_counts=final_state.get("skill_rule_counts", {}),
        )

    def agent_options(self) -> list[dict[str, Any]]:
        return [
            {
                "agent_name": spec.name,
                "title": spec.title,
                "default": spec.default,
            }
            for spec in self.agent_specs
        ]

    def _selected_specs(self, selected_agent_names: list[str] | None) -> list[AgentSpec]:
        if not selected_agent_names:
            return self.agent_specs
        selected = {name for name in selected_agent_names if name}
        specs = [spec for spec in self.agent_specs if spec.name in selected]
        return specs or self.agent_specs

    def _compile_graph(self, agent_specs: list[AgentSpec] | None = None):
        specs = agent_specs or self.agent_specs
        graph = StateGraph(AgentState)
        graph.add_node("orchestrator", self._orchestrator_node)
        for spec in specs:
            graph.add_node(spec.name, self._agent_node(spec))
        graph.add_node("finish", self._finish_node)

        graph.add_edge(START, "orchestrator")
        previous = "orchestrator"
        for spec in specs:
            graph.add_edge(previous, spec.name)
            previous = spec.name
        graph.add_edge(previous, "finish")
        graph.add_edge("finish", END)
        return graph.compile()

    def _orchestrator_node(self, state: AgentState) -> AgentState:
        snapshot = state.get("rule_snapshot")
        state["all_rules"] = rules_from_snapshot(snapshot) if snapshot else self.repository.select_rules()
        state["assigned_rule_ids"] = set()
        state["evaluations"] = []
        state["agent_counts"] = {}
        state["skill_rule_counts"] = {}
        state["events"] = [
            _event(
                "agent_started",
                "orchestrator",
                "LangGraph supervisor 启动",
                "加载固定 DAG，并从本次冻结规则快照向专业 Agent 分配规则子集。",
                stage_key="expert_orchestration",
                action_label="总控启动",
            )
        ]
        return state

    def _agent_node(self, spec: AgentSpec):
        def run_agent(state: AgentState) -> AgentState:
            all_rules = state.get("all_rules", [])
            assigned_rule_ids = state.get("assigned_rule_ids", set())
            skill = skill_for_agent(spec.name)
            rules = rules_for_skill(skill, all_rules, assigned_rule_ids) if skill else _rules_for_spec(spec, all_rules, assigned_rule_ids)
            if not spec.default:
                assigned_rule_ids.update(rule.rule_id for rule in rules)
            events = state.get("events", [])
            if skill:
                events.append(
                    _event(
                        "rules_assigned",
                        spec.name,
                        f"{skill.role_zh}已获取风险规则",
                        f"通过 {skill.skill_id} 从冻结规则快照读取 {len(rules)} 条规则。",
                        stage_key="expert_orchestration",
                        skill_id=skill.skill_id,
                        agent_role=skill.role_zh,
                        action_label="规则分配",
                        payload=skill_payload(skill, len(rules), rules),
                    )
                )
            events.append(
                _event(
                    "agent_started",
                    spec.name,
                    f"{spec.name} 开始分析",
                    f"读取 {len(rules)} 条冻结规则，按 Skill 检索候选证据并输出结构化 RiskFinding。",
                    stage_key="evidence_extraction",
                    skill_id=skill.skill_id if skill else None,
                    agent_role=skill.role_zh if skill else spec.title,
                    action_label="分析中",
                )
            )
            agent_evaluations = evaluate_text_against_rules(
                state.get("text", ""),
                rules=rules,
                repository=self.repository,
                rule_set_snapshot=state.get("rule_snapshot"),
            )
            for item in agent_evaluations:
                item.source_type = spec.name
                item.skill_id = skill.skill_id if skill else None
            triggered_count = sum(1 for item in agent_evaluations if item.evaluation_result == "triggered")
            agent_counts = state.get("agent_counts", {})
            agent_counts[spec.name] = triggered_count
            skill_rule_counts = state.get("skill_rule_counts", {})
            if skill:
                skill_rule_counts[skill.skill_id] = len(rules)
            state["skill_rule_counts"] = skill_rule_counts
            state["agent_counts"] = agent_counts
            state["assigned_rule_ids"] = assigned_rule_ids
            state["evaluations"] = state.get("evaluations", []) + agent_evaluations
            events.append(
                _event(
                    "agent_completed",
                    spec.name,
                    f"{spec.name} 分析完成",
                    f"生成 {triggered_count} 条结构化风险发现。",
                    stage_key="risk_identification",
                    skill_id=skill.skill_id if skill else None,
                    agent_role=skill.role_zh if skill else spec.title,
                    action_label="发现",
                    metrics={"triggered_findings": triggered_count, "rules_read": len(rules)},
                )
            )
            state["events"] = events
            return state

        return run_agent

    def _finish_node(self, state: AgentState) -> AgentState:
        events = state.get("events", [])
        events.append(
            _event(
                "agent_completed",
                "orchestrator",
                "Agent 团队分析完成",
                f"生成 {sum(state.get('agent_counts', {}).values())} 条候选风险发现。",
                stage_key="risk_identification",
                action_label="汇总",
            )
        )
        state["events"] = events
        return state


def _rules_for_spec(
    spec: AgentSpec,
    all_rules: list[RiskRule],
    assigned_rule_ids: set[str],
) -> list[RiskRule]:
    if spec.default:
        return [rule for rule in all_rules if rule.rule_id not in assigned_rule_ids]
    selected = []
    for rule in all_rules:
        if spec.rule_types and rule.rule_type in spec.rule_types:
            selected.append(rule)
            continue
        if spec.categories and rule.category in spec.categories:
            selected.append(rule)
    return selected


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
    event = {
        "event_type": event_type,
        "actor_name": actor_name,
        "title": title,
        "summary": summary,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    event.update({key: value for key, value in extra.items() if value is not None})
    return event
