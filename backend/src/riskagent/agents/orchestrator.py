"""Strategic agent contracts for dynamic expert selection and report synthesis."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from riskagent.agents.llm_resilience import call_llm_with_resilience
from riskagent.agents.prompts import render_orchestrator_prompt
from riskagent.providers.llm import get_default_llm_client
from riskagent.seed_data import AGENT_NAME_ALIASES


STRATEGY_SYSTEM_PROMPT = """\
你是 ProspectAlpha 的战略层 Agent「沈北辰」。你必须依据用户目标、招股书摘要和本次实时专家名册，
选择最小但完整的专家编队，并为每位专家分配一个互不重复的风险分析模块。不得使用名册之外的专家，
不得创建固定顺序或虚构能力。专家启动后会通过 A2A 邮箱自主通信，因此这里只定义主模块，不定义专家间固定连线。
名册中每位专家附有 runtime_status（实时状态）、call_count/avg_tokens（历史负载）：
runtime_status 为 WORKING 或 SHUTDOWN、或 available=false 的专家不得选用；负载仅作参考，能力匹配优先。

AVAILABLE_EXPERTS_JSON:
{available_experts_json}

只输出 JSON：
{"summary":str,"focus_areas":[str],"selected_agents":[str],"assignments":[
{"module_id":str,"agent_name":str,"module":str,"goal":str,"expected_outputs":[str]}
]}
"""


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrategyAssignment(StrictContract):
    module_id: str = Field(min_length=1, max_length=96)
    agent_name: str = Field(min_length=1, max_length=64)
    module: str = Field(min_length=1, max_length=160)
    goal: str = Field(min_length=1, max_length=1200)
    expected_outputs: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def normalize_module_id(self) -> "StrategyAssignment":
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", self.module_id):
            raise ValueError("module_id must contain only letters, numbers, underscore, or dash")
        return self


class StrategyDecision(StrictContract):
    summary: str = Field(min_length=1, max_length=2000)
    focus_areas: list[str] = Field(default_factory=list)
    selected_agents: list[str] = Field(min_length=1)
    assignments: list[StrategyAssignment] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_internal_consistency(self) -> "StrategyDecision":
        selected = [name.strip() for name in self.selected_agents]
        if any(not name for name in selected):
            raise ValueError("selected_agents must not contain blank names")
        if len(selected) != len(set(selected)):
            raise ValueError("selected_agents must be unique")
        assignment_names = [item.agent_name for item in self.assignments]
        if len(assignment_names) != len(set(assignment_names)):
            raise ValueError("each selected expert must receive exactly one primary assignment")
        if set(assignment_names) != set(selected):
            raise ValueError("assignments must cover every selected expert exactly once")
        module_ids = [item.module_id for item in self.assignments]
        if len(module_ids) != len(set(module_ids)):
            raise ValueError("assignment module_id values must be unique")
        self.selected_agents = selected
        return self


class ReplacementDecision(StrictContract):
    module_id: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1200)


class IntegratedReportSection(StrictContract):
    module_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    analysis: str = Field(min_length=1)
    finding_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class IntegratedCrossModuleRisk(StrictContract):
    title: str = Field(min_length=1)
    analysis: str = Field(min_length=1)
    finding_ids: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class IntegratedRiskReport(StrictContract):
    title: str = Field(min_length=1)
    executive_summary: str = Field(min_length=1)
    overall_risk_level: str = Field(min_length=1)
    sections: list[IntegratedReportSection]
    cross_module_risks: list[IntegratedCrossModuleRisk] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


# Compatibility aliases retained for callers during the API transition.
OrchestraStage = StrategyAssignment
OrchestraPlan = StrategyDecision


async def orchestrate(
    *,
    user_requirement: str,
    company_name: str,
    document_summary: str,
    available_agents: list[dict[str, Any]],
    on_delta: Callable[[str], None] | None = None,
    on_response: Callable[[str], None] | None = None,
) -> StrategyDecision:
    """Ask the strategic model for a plan and strictly validate it against the live roster."""

    if not available_agents:
        raise ValueError("No executable expert agents are available.")
    messages = _strategy_messages(
            user_requirement=user_requirement,
            company_name=company_name,
            document_summary=document_summary[:4000],
            available_agents=available_agents,
        )
    client = get_default_llm_client()
    if on_delta or on_response:
        raw, raw_response = await client.chat_json_stream(
            messages, temperature=0.1, on_delta=on_delta
        )
        if on_response:
            on_response(raw_response)
    else:
        raw = await client.chat_json(messages, temperature=0.1)
    return validate_strategy_decision(raw, available_agents)


def validate_strategy_decision(
    raw: dict[str, Any] | StrategyDecision,
    available_agents: list[dict[str, Any]],
) -> StrategyDecision:
    available = {
        str(item.get("agent_name", "")).strip()
        for item in available_agents
        if str(item.get("agent_name", "")).strip()
    }
    plan = _normalize_strategy_decision(raw, available)
    unknown = sorted(set(plan.selected_agents) - available)
    if unknown:
        raise ValueError(f"Strategy selected unavailable experts: {', '.join(unknown)}")
    return plan


def manual_strategy_decision(
    *,
    agent_names: list[str],
    available_agents: list[dict[str, Any]],
    summary: str = "战略规划中断后由用户选择专家，系统依据 Agent Card 自动派题。",
) -> StrategyDecision:
    by_name = {
        str(item.get("agent_name", "")).strip(): item
        for item in available_agents
        if str(item.get("agent_name", "")).strip()
    }
    selected = [_resolve_agent_name(name.strip(), by_name) for name in agent_names]
    if not selected or any(not name for name in selected):
        raise ValueError("Every selected expert name must be non-empty.")
    if len(selected) != len(set(selected)):
        raise ValueError("Selected expert names must be unique.")
    unknown = sorted(set(selected) - set(by_name))
    if unknown:
        raise ValueError(f"Unknown experts: {', '.join(unknown)}")
    assignments = []
    focus_areas = []
    for name in selected:
        spec = by_name[name]
        role = str(spec.get("role") or spec.get("title") or name)
        capabilities = [str(item) for item in spec.get("capabilities") or []]
        focus = "、".join(capabilities[:5]) or role
        focus_areas.append(focus)
        assignments.append(
            StrategyAssignment(
                module_id=f"module_{name.removesuffix('_agent')}",
                agent_name=name,
                module=role,
                goal=f"围绕 {focus} 独立分析招股书风险，并返回可追溯 finding 与 evidence 引用。",
                expected_outputs=[
                    str(spec.get("output_schema") or "RiskFinding + RunEvidence"),
                    "模块分析摘要与局限性",
                ],
            )
        )
    return StrategyDecision(
        summary=summary,
        focus_areas=focus_areas,
        selected_agents=selected,
        assignments=assignments,
    )


def manual_recovery_strategy_decision(
    *,
    agent_names: list[str],
    failed_assignments: list[StrategyAssignment],
    available_agents: list[dict[str, Any]],
) -> StrategyDecision:
    """Map human-selected experts onto failed modules using live Agent Cards."""

    by_name = {
        str(item.get("agent_name", "")).strip(): item
        for item in available_agents
        if str(item.get("agent_name", "")).strip()
    }
    selected = [_resolve_agent_name(name.strip(), by_name) for name in agent_names]
    if not selected or any(not name for name in selected):
        raise ValueError("Every selected expert name must be non-empty.")
    if len(selected) != len(set(selected)):
        raise ValueError("Selected expert names must be unique.")
    unknown = sorted(set(selected) - set(by_name))
    if unknown:
        raise ValueError(f"Unknown experts: {', '.join(unknown)}")
    if len(selected) != len(failed_assignments):
        raise ValueError(
            "Manual recovery requires exactly one selected expert per failed module: "
            f"selected={len(selected)}, failed_modules={len(failed_assignments)}"
        )

    remaining = set(selected)
    ordered_names: list[str] = []
    for assignment in failed_assignments:
        name = sorted(
            remaining,
            key=lambda candidate: (
                -_capability_match_score(by_name[candidate], assignment),
                candidate,
            ),
        )[0]
        ordered_names.append(name)
        remaining.remove(name)
    assignments = []
    for name, failed in zip(ordered_names, failed_assignments, strict=True):
        card = by_name[name]
        capabilities = [str(item) for item in card.get("capabilities") or []]
        capability_summary = "、".join(capabilities[:5]) or str(
            card.get("role") or card.get("title") or name
        )
        assignments.append(
            failed.model_copy(
                update={
                    "agent_name": name,
                    "goal": (
                        f"{failed.goal} 人工恢复阶段由 {name} 承接；"
                        f"结合其 Agent Card 能力（{capability_summary}）完成原模块。"
                    ),
                    "expected_outputs": list(
                        dict.fromkeys(
                            [
                                *failed.expected_outputs,
                                str(card.get("output_schema") or "RiskFinding + RunEvidence"),
                            ]
                        )
                    ),
                }
            )
        )
    return StrategyDecision(
        summary="动态替补耗尽后由用户选择专家，系统按 Agent Card 映射到未完成模块。",
        focus_areas=[assignment.module for assignment in failed_assignments],
        selected_agents=list(ordered_names),
        assignments=assignments,
    )


def _normalize_strategy_decision(
    raw: dict[str, Any] | StrategyDecision,
    available: set[str],
) -> StrategyDecision:
    payload = raw.model_dump() if isinstance(raw, StrategyDecision) else dict(raw)
    roster = {name: {} for name in available}
    payload["selected_agents"] = [
        _resolve_agent_name(str(name).strip(), roster) for name in payload.get("selected_agents", [])
    ]
    payload["assignments"] = [
        {
            **dict(item),
            "agent_name": _resolve_agent_name(str(item.get("agent_name", "")).strip(), roster),
        }
        for item in payload.get("assignments", [])
    ]
    return StrategyDecision.model_validate(payload)


def _capability_match_score(
    agent: dict[str, Any], assignment: StrategyAssignment
) -> int:
    target = " ".join(
        [assignment.module, assignment.goal, *assignment.expected_outputs]
    ).lower()
    terms = [
        str(value).strip().lower()
        for key in ("capabilities", "rule_types", "categories")
        for value in agent.get(key) or []
        if str(value).strip()
    ]
    description = " ".join(
        str(agent.get(key) or "").lower()
        for key in ("role", "title", "description", "output_schema")
    )
    return sum(3 for term in terms if term in target) + sum(
        1 for token in re.findall(r"[a-z0-9_\u4e00-\u9fff]+", target) if token in description
    )


async def select_replacement(
    *,
    company_name: str,
    failed_assignment: StrategyAssignment,
    failure_reason: str,
    available_candidates: list[dict[str, Any]],
) -> ReplacementDecision:
    if not available_candidates:
        raise ValueError("No untried replacement experts are available.")
    roster = json.dumps(available_candidates, ensure_ascii=False)
    raw = await get_default_llm_client().chat_json(
        [
            {
                "role": "system",
                "content": (
                    "你是 ProspectAlpha 战略层 Agent。为失败模块选择一位能力匹配且尚未尝试的替补专家。"
                    "agent_name 必须来自候选名册；module_id 必须保持不变。只输出 JSON "
                    '{"module_id":str,"agent_name":str,"reason":str}。'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"公司：{company_name}\n失败任务：{failed_assignment.model_dump_json()}\n"
                    f"失败原因：{failure_reason}\n候选专家：{roster}"
                ),
            },
        ],
        temperature=0.1,
    )
    return validate_replacement_decision(
        raw,
        failed_assignment=failed_assignment,
        available_candidates=available_candidates,
    )


def validate_replacement_decision(
    value: ReplacementDecision | dict[str, Any],
    *,
    failed_assignment: StrategyAssignment,
    available_candidates: list[dict[str, Any]],
) -> ReplacementDecision:
    decision = ReplacementDecision.model_validate(value)
    candidates = {
        str(item.get("agent_name", "")).strip()
        for item in available_candidates
        if str(item.get("agent_name", "")).strip()
    }
    if decision.module_id != failed_assignment.module_id:
        raise ValueError("Replacement must preserve module_id.")
    if decision.agent_name not in candidates:
        raise ValueError("Replacement expert is not in the eligible candidate roster.")
    return decision


def _resolve_agent_name(agent_name: str, roster: dict[str, Any]) -> str:
    if agent_name in roster:
        return agent_name
    alias = AGENT_NAME_ALIASES.get(agent_name)
    if alias and alias in roster:
        return alias
    return agent_name


async def synthesize_report(
    *,
    company_name: str,
    data_cutoff_date: str,
    module_results: list[dict[str, Any]],
    verified_findings: list[dict[str, Any]],
    evidence_index: list[dict[str, Any]],
    validation_feedback: str | None = None,
) -> IntegratedRiskReport:
    raw = await get_default_llm_client().chat_json(
        _report_messages(
            company_name=company_name,
            data_cutoff_date=data_cutoff_date,
            module_results=module_results,
            verified_findings=verified_findings,
            evidence_index=evidence_index,
            validation_feedback=validation_feedback,
        ),
        temperature=0.2,
    )
    return IntegratedRiskReport.model_validate(raw)


def validate_report_references(
    report: IntegratedRiskReport,
    *,
    module_ids: set[str],
    finding_ids: set[str],
    finding_ids_by_module: Mapping[str, set[str]],
    evidence_by_finding: Mapping[str, set[str]],
) -> None:
    section_module_ids = [section.module_id for section in report.sections]
    duplicate_modules = sorted(
        module_id
        for module_id in set(section_module_ids)
        if section_module_ids.count(module_id) > 1
    )
    missing_modules = sorted(module_ids - set(section_module_ids))
    unknown_modules = sorted(set(section_module_ids) - module_ids)
    if duplicate_modules or missing_modules or unknown_modules:
        raise ValueError(
            "Integrated report must cover every expert module exactly once: "
            f"duplicates={duplicate_modules}, missing={missing_modules}, "
            f"unknown={unknown_modules}"
        )
    referenced_findings = {
        finding_id
        for section in [*report.sections, *report.cross_module_risks]
        for finding_id in section.finding_ids
    }
    referenced_evidence = {
        evidence_id
        for section in [*report.sections, *report.cross_module_risks]
        for evidence_id in section.evidence_ids
    }
    evidence_ids = {
        evidence_id
        for linked_ids in evidence_by_finding.values()
        for evidence_id in linked_ids
    }
    unknown_findings = sorted(referenced_findings - finding_ids)
    unknown_evidence = sorted(referenced_evidence - evidence_ids)
    if unknown_findings or unknown_evidence:
        raise ValueError(
            "Integrated report contains unknown references: "
            f"findings={unknown_findings}, evidence={unknown_evidence}"
        )
    foreign_module_findings = {
        section.module_id: sorted(
            set(section.finding_ids)
            - finding_ids_by_module.get(section.module_id, set())
        )
        for section in report.sections
        if set(section.finding_ids)
        - finding_ids_by_module.get(section.module_id, set())
    }
    if foreign_module_findings:
        raise ValueError(
            "Integrated report section contains findings from another module: "
            f"{foreign_module_findings}"
        )
    mismatched_evidence: list[str] = []
    findings_without_evidence: list[str] = []
    for section in [*report.sections, *report.cross_module_risks]:
        section_findings = set(section.finding_ids)
        section_evidence = set(section.evidence_ids)
        for finding_id in section_findings:
            if not section_evidence.intersection(
                evidence_by_finding.get(finding_id, set())
            ):
                findings_without_evidence.append(finding_id)
        for evidence_id in section_evidence:
            owners = {
                finding_id
                for finding_id, linked_ids in evidence_by_finding.items()
                if evidence_id in linked_ids
            }
            if not owners.intersection(section_findings):
                mismatched_evidence.append(evidence_id)
    if mismatched_evidence or findings_without_evidence:
        raise ValueError(
            "Integrated report evidence must belong to a finding in the same section: "
            f"mismatched_evidence={sorted(set(mismatched_evidence))}, "
            "findings_without_section_evidence="
            f"{sorted(set(findings_without_evidence))}"
        )


def _strategy_messages(
    *,
    user_requirement: str,
    company_name: str,
    document_summary: str,
    available_agents: list[dict[str, Any]],
) -> list[dict[str, str]]:
    roster = json.dumps(available_agents, ensure_ascii=False, sort_keys=True)
    template = render_orchestrator_prompt("strategy_system", STRATEGY_SYSTEM_PROMPT)
    return [
        {
            "role": "system",
            "content": template.replace("{available_experts_json}", roster),
        },
        {
            "role": "user",
            "content": (
                f"公司名称：{company_name}\n"
                f"用户需求：{user_requirement}\n"
                f"招股书摘要：{document_summary}"
            ),
        },
    ]


REPORT_SYSTEM_PROMPT = (
    "你是 ProspectAlpha 战略层 Agent「沈北辰」。整合所有专家模块结果，输出完整、连贯的 IPO 风险报告。"
    "不得新增输入中不存在的 finding_id 或 evidence_id；每个专家模块都必须有对应 section，即使结论是未发现重大风险。"
    "每个被引用的 finding 必须在同一 section 中附带至少一条归属于它的 evidence；没有可验证引用时不得创建跨模块风险。"
    "module summary 已经过证据门禁清洗；不得依据没有 finding_id 的模块摘要宣称存在或不存在风险。"
    "overall_risk_level 必须由 verified_findings 推导；为空时只能输出未评级，不得输出低/中/高。"
    "只输出 JSON："
    '{"title":str,"executive_summary":str,"overall_risk_level":str,'
    '"sections":[{"module_id":str,"title":str,"analysis":str,"finding_ids":[str],"evidence_ids":[str]}],'
    '"cross_module_risks":[{"title":str,"analysis":str,"finding_ids":[str],"evidence_ids":[str]}],'
    '"recommendations":[str],"limitations":[str]}。'
)


def _report_messages(
    *,
    company_name: str,
    data_cutoff_date: str,
    module_results: list[dict[str, Any]],
    verified_findings: list[dict[str, Any]],
    evidence_index: list[dict[str, Any]],
    validation_feedback: str | None = None,
) -> list[dict[str, str]]:
    payload = json.dumps(
        {
            "company_name": company_name,
            "data_cutoff_date": data_cutoff_date,
            "module_results": module_results,
            "verified_findings": verified_findings,
            "evidence_index": evidence_index,
        },
        ensure_ascii=False,
    )
    messages = [
        {
            "role": "system",
            "content": render_orchestrator_prompt("report_system", REPORT_SYSTEM_PROMPT),
        },
        {"role": "user", "content": payload},
    ]
    if validation_feedback:
        messages.append(
            {
                "role": "user",
                "content": (
                    f"上一次输出未通过校验：{validation_feedback}。"
                    "请修正后重新输出完整 JSON，只引用输入中存在的 finding_id/evidence_id。"
                ),
            }
        )
    return messages


# ============================================================================
# Debate-phase contracts (Task 3): peer critique, finding revision, arbiter.
# ============================================================================


class PeerCritique(StrictContract):
    critic_agent: str = Field(min_length=1, max_length=64)
    target_agent: str = Field(min_length=1, max_length=64)
    finding_ids: list[str] = Field(min_length=1)
    critique_summary: str = Field(min_length=1, max_length=2000)
    suggested_revisions: list[str] = Field(default_factory=list)
    severity: Literal["info", "minor", "major", "blocker"] = "info"


class FindingRevision(StrictContract):
    agent_name: str = Field(min_length=1, max_length=64)
    original_finding_ids: list[str] = Field(default_factory=list)
    revised_summary: str = Field(min_length=1, max_length=4000)
    added_evidence_ids: list[str] = Field(default_factory=list)
    removed_finding_ids: list[str] = Field(default_factory=list)
    revision_reason: str = Field(min_length=1, max_length=2000)


class ArbiterRuling(StrictContract):
    decision: Literal["proceed", "request_supplement", "escalate_human"]
    reasoning: str = Field(min_length=1, max_length=4000)
    required_supplements: list[str] = Field(default_factory=list)
    flagged_finding_ids: list[str] = Field(default_factory=list)
    approved_finding_ids: list[str] = Field(default_factory=list)


ARBITER_SYSTEM_PROMPT = """\
你是 ProspectAlpha 战略层 Agent「周慎言」，担任质量总检·红队角色。你的职责是审阅专家团队两轮辩论后的 finding、质证意见与修订记录，
判断当前证据链是否足以立论，并给出明确裁决。不得创建新 finding；只能基于已有证据给出 proceed / request_supplement / escalate_human。

裁决准则：
- proceed：所有重大 finding 至少有一条互证证据、质证意见已得到合理回应。
- request_supplement：仍有 finding 缺少原文证据、缺少交叉验证或质证未回应；指出需要补充的内容。
- escalate_human：存在不可调和的分歧或严重事实冲突，需要人工接管。

只输出 JSON：
{"decision":"proceed"|"request_supplement"|"escalate_human","reasoning":str,
"required_supplements":[str],"flagged_finding_ids":[str],"approved_finding_ids":[str]}
"""


async def arbitrate_debate(
    *,
    company_name: str,
    findings_with_critiques: list[dict[str, Any]],
    arbiter_agent_name: str = "zhou_shenyan_redteam_agent",
    live_call: Any = None,
) -> ArbiterRuling:
    """Invoke the L3 red-team arbiter to rule on the debate outcome."""

    payload = json.dumps(
        {
            "company_name": company_name,
            "arbiter_agent_name": arbiter_agent_name,
            "findings_with_critiques": findings_with_critiques,
        },
        ensure_ascii=False,
    )
    try:
        raw = await call_llm_with_resilience(
            messages=[
                {"role": "system", "content": render_orchestrator_prompt("arbiter_system", ARBITER_SYSTEM_PROMPT)},
                {"role": "user", "content": payload},
            ],
            temperature=0.1,
            context={"phase": "arbitration", "arbiter": arbiter_agent_name},
            on_delta=live_call.delta if live_call else None,
            on_response=live_call.complete if live_call else None,
        )
    except Exception as exc:
        if live_call:
            live_call.fail(exc)
        raise
    return ArbiterRuling.model_validate(raw)
