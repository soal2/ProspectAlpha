"""Agent team supervisor for fixed HK-IPO risk analysis DAG."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

import yaml
from langgraph.graph import END, START, StateGraph

from riskagent.risk_rules import RiskRule, RiskRuleRepository
from riskagent.skills.risk_extract import RuleEvaluation, evaluate_text_against_rules


BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AGENT_GRAPH_PATH = BACKEND_ROOT / "configs" / "agent_graph.yaml"


@dataclass(frozen=True)
class AgentSpec:
    name: str
    title: str
    rule_types: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    default: bool = False


@dataclass
class AgentTeamResult:
    evaluations: list[RuleEvaluation]
    events: list[dict[str, Any]]
    agent_counts: dict[str, int]


class AgentState(TypedDict, total=False):
    text: str
    max_findings: int
    all_rules: list[RiskRule]
    assigned_rule_ids: set[str]
    evaluations: list[RuleEvaluation]
    events: list[dict[str, Any]]
    agent_counts: dict[str, int]


class AgentTeamSupervisor:
    def __init__(
        self,
        *,
        repository: RiskRuleRepository,
        graph_path: Path | str | None = None,
    ) -> None:
        self.repository = repository
        self.graph_path = Path(graph_path or DEFAULT_AGENT_GRAPH_PATH)
        self.agent_specs = _load_agent_specs(self.graph_path)
        self.graph = self._compile_graph()

    def run(
        self,
        *,
        text: str,
        max_findings: int,
        selected_agent_names: list[str] | None = None,
    ) -> AgentTeamResult:
        specs = self._selected_specs(selected_agent_names)
        graph = self.graph if len(specs) == len(self.agent_specs) else self._compile_graph(specs)
        final_state = graph.invoke({"text": text, "max_findings": max_findings})
        return AgentTeamResult(
            evaluations=_dedupe_evaluations(final_state.get("evaluations", []))[: max_findings * 4],
            events=final_state.get("events", []),
            agent_counts=final_state.get("agent_counts", {}),
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
        state["all_rules"] = self.repository.select_rules()
        state["assigned_rule_ids"] = set()
        state["evaluations"] = []
        state["agent_counts"] = {}
        state["events"] = [
            _event(
                "agent_started",
                "orchestrator",
                "LangGraph supervisor 启动",
                "加载固定 DAG 并向专业 Agent 分配规则子集。",
            )
        ]
        return state

    def _agent_node(self, spec: AgentSpec):
        def run_agent(state: AgentState) -> AgentState:
            all_rules = state.get("all_rules", [])
            assigned_rule_ids = state.get("assigned_rule_ids", set())
            rules = _rules_for_spec(spec, all_rules, assigned_rule_ids)
            if not spec.default:
                assigned_rule_ids.update(rule.rule_id for rule in rules)
            events = state.get("events", [])
            events.append(
                _event(
                    "agent_started",
                    spec.name,
                    f"{spec.name} 开始分析",
                    f"读取 {len(rules)} 条规则、检索候选证据并输出结构化 RiskFinding。",
                )
            )
            agent_evaluations = evaluate_text_against_rules(
                state.get("text", ""),
                rules=rules,
                repository=self.repository,
            )
            for item in agent_evaluations:
                item.source_type = spec.name
            triggered_count = sum(1 for item in agent_evaluations if item.evaluation_result == "triggered")
            agent_counts = state.get("agent_counts", {})
            agent_counts[spec.name] = triggered_count
            state["agent_counts"] = agent_counts
            state["assigned_rule_ids"] = assigned_rule_ids
            state["evaluations"] = state.get("evaluations", []) + agent_evaluations
            events.append(
                _event(
                    "agent_completed",
                    spec.name,
                    f"{spec.name} 分析完成",
                    f"生成 {triggered_count} 条结构化风险发现。",
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
            )
        )
        state["events"] = events
        return state


def _load_agent_specs(path: Path) -> list[AgentSpec]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    specs = []
    for item in data.get("agents", []):
        specs.append(
            AgentSpec(
                name=item["name"],
                title=item.get("title", item["name"]),
                rule_types=tuple(item.get("rule_types", [])),
                categories=tuple(item.get("categories", [])),
                default=bool(item.get("default", False)),
            )
        )
    return specs


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


def _dedupe_evaluations(evaluations: list[RuleEvaluation]) -> list[RuleEvaluation]:
    seen: set[str] = set()
    deduped = []
    for item in evaluations:
        if item.rule_id in seen:
            continue
        seen.add(item.rule_id)
        deduped.append(item)
    return deduped


def _event(event_type: str, actor_name: str, title: str, summary: str) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "actor_name": actor_name,
        "title": title,
        "summary": summary,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
