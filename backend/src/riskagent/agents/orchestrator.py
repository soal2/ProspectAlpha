"""Strategic agent contracts for dynamic expert selection and report synthesis."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from riskagent.agents.llm_resilience import call_llm_with_resilience
from riskagent.agents.prompts import render_orchestrator_prompt
from riskagent.providers.llm import get_default_llm_client
from riskagent.seed_data import AGENT_NAME_ALIASES


# ---------------------------------------------------------------------------
# 5x5 Probability-Impact Matrix
# ---------------------------------------------------------------------------

PROBABILITY_LABELS: dict[int, str] = {
    1: "极不可能",    # <10%
    2: "不太可能",    # 10-30%
    3: "可能",        # 30-50%
    4: "很可能",      # 50-80%
    5: "几乎确定",    # >80%
}

IMPACT_LABELS: dict[int, str] = {
    1: "可忽略",      # negligible impact
    2: "轻微",        # minor impact
    3: "中等",        # moderate impact
    4: "重大",        # significant impact
    5: "灾难性",      # catastrophic impact
}

# Matrix: risk_level for each (probability, impact) combination
# Indexed as RISK_MATRIX[probability][impact]
RISK_MATRIX: dict[int, dict[int, str]] = {
    1: {1: "very_low",  2: "very_low",  3: "low",      4: "low",      5: "medium"},
    2: {1: "very_low",  2: "low",       3: "low",      4: "medium",   5: "medium"},
    3: {1: "low",       2: "low",       3: "medium",   4: "medium",   5: "high"},
    4: {1: "low",       2: "medium",    3: "medium",   4: "high",     5: "high"},
    5: {1: "medium",    2: "medium",    3: "high",     4: "high",     5: "very_high"},
}


def assess_probability_impact(
    probability: int,  # 1-5
    impact: int,       # 1-5
) -> tuple[str, str, str]:
    """Look up the 5x5 matrix and return (risk_level, probability_label, impact_label)."""
    probability = max(1, min(5, probability))
    impact = max(1, min(5, impact))
    risk_level = RISK_MATRIX[probability][impact]
    return risk_level, PROBABILITY_LABELS[probability], IMPACT_LABELS[impact]


def aggregate_risk_by_matrix(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate risk findings using the probability-impact matrix.

    Returns a dict with overall_probability, overall_impact, matrix_risk_level,
    risk_distribution and top_risks.
    """
    empty_distribution = {"very_high": 0, "high": 0, "medium": 0, "low": 0, "very_low": 0}
    if not findings:
        return {
            "overall_probability": 1,
            "overall_impact": 1,
            "matrix_risk_level": "very_low",
            "risk_distribution": empty_distribution,
            "top_risks": [],
        }

    risk_levels: list[dict[str, Any]] = []
    for f in findings:
        prob = max(1, min(5, int(f.get("probability", 3))))
        imp = max(1, min(5, int(f.get("impact", 3))))
        level = RISK_MATRIX[prob][imp]
        risk_levels.append({
            "probability": prob,
            "impact": imp,
            "risk_level": level,
            "description": f.get("description", f.get("title", "")),
        })

    # Overall = rounded average of probability and impact
    avg_prob = sum(r["probability"] for r in risk_levels) / len(risk_levels)
    avg_impact = sum(r["impact"] for r in risk_levels) / len(risk_levels)
    overall_prob = max(1, min(5, round(avg_prob)))
    overall_impact = max(1, min(5, round(avg_impact)))
    overall_level = RISK_MATRIX[overall_prob][overall_impact]

    # Distribution
    distribution = dict(empty_distribution)
    for r in risk_levels:
        distribution[r["risk_level"]] += 1

    # Top risks sorted by severity (very_high first)
    severity_order = {"very_high": 0, "high": 1, "medium": 2, "low": 3, "very_low": 4}
    top_risks = sorted(
        risk_levels,
        key=lambda x: severity_order.get(x["risk_level"], 5),
    )[:5]

    return {
        "overall_probability": overall_prob,
        "overall_impact": overall_impact,
        "matrix_risk_level": overall_level,
        "risk_distribution": distribution,
        "top_risks": top_risks,
    }


# ---------------------------------------------------------------------------
# 5-level risk rating system
# ---------------------------------------------------------------------------

# Canonical level vocabulary and thresholds are owned by riskagent.common so the
# report rating and the frozen backtest scorecard can never drift apart.
from riskagent.common.risk_levels import (  # noqa: E402
    LEGACY_RISK_LEVEL_MAP as _LEGACY_RISK_LEVEL_MAP,
    RiskLevel,
    SCORE_THRESHOLDS,
    map_legacy_risk_level,
    score_to_risk_level,
)

# Severity weight for quantitative scoring
SEVERITY_WEIGHTS: dict[str, int] = {
    "very_low": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "very_high": 5,
}


def calculate_risk_score(
    findings: list[dict[str, Any]],
    rules: list[dict[str, Any]] | None = None,
) -> float:
    """Compute a quantitative risk score in [0, 100].

    Formula (per finding):
        contribution = severity_weight * confidence * rule_weight
    The raw sum is normalised to 0-100.

    Parameters
    ----------
    findings:
        Each dict should contain at least ``severity`` (str) and
        ``confidence`` (float 0-1).  An optional ``rule_weight`` (float,
        default 1.0) allows certain rules to carry more influence.
    rules:
        Optional list of rule definitions.  If a finding carries a
        ``rule_id`` that appears in *rules*, the rule's ``weight`` (if
        present) is used as ``rule_weight``.
    """
    if not findings:
        return 0.0

    rule_weight_map: dict[str, float] = {}
    if rules:
        for rule in rules:
            rid = rule.get("rule_id") or rule.get("id")
            if rid:
                rule_weight_map[str(rid)] = float(rule.get("weight", 1.0))

    raw_scores: list[float] = []
    for finding in findings:
        severity = str(finding.get("severity", "medium")).lower()
        confidence = float(finding.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        severity_weight = SEVERITY_WEIGHTS.get(severity, 3)

        rule_id = finding.get("rule_id")
        rule_weight = rule_weight_map.get(str(rule_id), 1.0) if rule_id else 1.0

        raw_scores.append(severity_weight * confidence * rule_weight)

    # Normalise: max possible per finding = 5 (severity) * 1 (conf) * 5 (rule_weight cap)
    # We cap rule_weight at 5 for normalisation purposes.
    max_per_finding = 5.0 * 1.0 * 5.0  # 25
    normalised = sum(raw_scores) / (len(findings) * max_per_finding) * 100.0
    return round(max(0.0, min(100.0, normalised)), 2)


# ---------------------------------------------------------------------------
# Dynamic expert selection based on company characteristics
# ---------------------------------------------------------------------------

import logging

_logger = logging.getLogger(__name__)


def _infer_company_characteristics(
    company_name: str | None,
    focus: str | None,
    documents: list | None = None,
) -> dict[str, Any]:
    """Infer company characteristics for expert selection.

    Uses simple keyword matching on company name and focus areas to determine
    industry-specific traits that influence expert team composition.
    """
    chars: dict[str, Any] = {
        "has_biotech": False,
        "has_vie_structure": False,
        "company_scale": None,
        "industry": None,
    }
    text = f"{company_name or ''} {focus or ''}".lower()

    biotech_keywords = ["生物", "医药", "基因", "细胞", "生物科技", "biotech", "pharma", "制药"]
    if any(kw in text for kw in biotech_keywords):
        chars["has_biotech"] = True

    vie_keywords = ["vie", "协议控制", "可变利益实体"]
    if any(kw in text for kw in vie_keywords):
        chars["has_vie_structure"] = True

    return chars


def select_experts_for_strategy(
    company_industry: str | None = None,
    company_scale: str | None = None,  # "large_cap", "mid_cap", "small_cap"
    has_biotech: bool = False,
    has_vie_structure: bool = False,
    market_data_available: bool = False,
    focus_areas: list[str] | None = None,
) -> list[str]:
    """Dynamically select the optimal expert team based on analysis target characteristics.

    Returns list of agent IDs to include in the analysis.

    Rules:
    - Always include: 财务分析(qin_bingwen), 法务合规(wen_shouzheng), 市场情报(lu_guanlan), 证据检索(cheng_zheng)
    - Biotech companies: add 技术管线(ye_zhiyuan)
    - VIE structure: ensure 法务合规 gets higher priority (already in core)
    - Market data available: ensure 行情专家(bai_shice) is included
    - Large cap: add 业务策略(su_zhiji) for complex business analysis
    - Focus areas: map to relevant specialist agents
    """
    # Core team — always included
    core_agents = [
        "qin_bingwen_financial_agent",
        "wen_shouzheng_legal_agent",
        "lu_guanlan_market_agent",
        "cheng_zheng_evidence_agent",
    ]

    selected = list(core_agents)

    # Industry-specific additions
    if has_biotech:
        if "ye_zhiyuan_business_agent" not in selected:
            selected.append("ye_zhiyuan_business_agent")

    # Market data
    if market_data_available:
        if "bai_shice_market_agent" not in selected:
            selected.append("bai_shice_market_agent")

    # Scale-based additions
    if company_scale in ("large_cap", "mid_cap"):
        if "su_zhiji_business_agent" not in selected:
            selected.append("su_zhiji_business_agent")

    # L2/L3 agents — always include for quality
    l2_l3 = [
        "jiang_hengzhi_market_agent",
        "gu_lingsheng_market_agent",
        "xiao_zeming_quality_agent",
        "zhou_shenyan_redteam_agent",
    ]
    for agent in l2_l3:
        if agent not in selected:
            selected.append(agent)

    # Focus-area-based additions
    if focus_areas:
        if any("ESG" in f or "环境" in f or "社会" in f for f in focus_areas):
            if "esg_agent" not in selected:
                selected.append("esg_agent")
        if any("宏观" in f or "经济" in f or "利率" in f for f in focus_areas):
            if "macro_economy_agent" not in selected:
                selected.append("macro_economy_agent")

    _logger.info(
        "Dynamic expert selection: selected %d agents based on characteristics "
        "(industry=%s, scale=%s, biotech=%s, vie=%s, market_data=%s)",
        len(selected),
        company_industry,
        company_scale,
        has_biotech,
        has_vie_structure,
        market_data_available,
    )
    return selected


STRATEGY_SYSTEM_PROMPT = """\
你是 ProspectAlpha 战略层 Agent「沈北辰」。你必须依据用户目标、招股书摘要和本次实时专家名册，
选择最小但完整的专家编队，并为每位专家分配一个互不重复的风险分析模块。不得使用名册之外的专家，
不得创建固定顺序或虚构能力。专家启动后会通过 A2A 邮箱自主通信，因此这里只定义主模块，不定义专家间固定连线。
名册中每位专家附有 runtime_status（实时状态）、call_count/avg_tokens（历史负载）：
runtime_status 为 WORKING 或 SHUTDOWN、或 available=false 的专家不得选用；负载仅作参考，能力匹配优先。

【强制约束】市场情绪分析是跨模态共振的必要输入。只要名册中存在可用的市场情绪类专家
（如白时策、陆观澜、顾聆声、江衡之），就必须选择至少一位，不得跳过。

{recommended_agents_hint}

AVAILABLE_EXPERTS_JSON:
{available_experts_json}

skill_ids 必须来自对应 Agent Card 的 capabilities，且不得包含离线 Skill。
只输出 JSON：
{"summary":str,"focus_areas":[str],"selected_agents":[str],"assignments":[
{"module_id":str,"agent_name":str,"module":str,"goal":str,"expected_outputs":[str],"skill_ids":[str]}
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
    skill_ids: list[str] = Field(default_factory=list)

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


class IntegratedRiskTheme(StrictContract):
    title: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    analysis: str = Field(min_length=1)
    investment_impact: str = Field(min_length=1)
    uncertainties: list[str] = Field(default_factory=list)
    monitoring_points: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_unique_references(self) -> "IntegratedRiskTheme":
        if len(self.finding_ids) != len(set(self.finding_ids)):
            raise ValueError("Risk theme finding_ids must be unique")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("Risk theme evidence_ids must be unique")
        return self


class CrossModalAnalysis(StrictContract):
    """跨模态共振分析：基本面静态文本 × 市场情绪动态时序。"""
    consistency_assessment: str = Field(
        min_length=1,
        description="基本面与市场情绪一致性判断",
    )
    resonance_risks: list[str] = Field(
        default_factory=list,
        description="共振风险信号：基本面风险 + 市场冷淡 = 高风险",
    )
    divergence_signals: list[str] = Field(
        default_factory=list,
        description="背离信号：基本面与市场情绪不一致，需说明不确定性并补证",
    )
    market_data_note: str = Field(
        default="",
        description="市场数据可用性说明（如数据不可用则注明）",
    )


class PostIPORiskAssessment(StrictContract):
    """上市后风险预测评估：聚焦5个交易日内显著下跌风险与破发风险。"""
    five_day_decline_risk: Literal["high", "medium", "low"] = Field(
        default="medium",
        description="上市后5个交易日内显著下跌风险",
    )
    twenty_day_decline_risk: Literal["high", "medium", "low"] = Field(
        default="medium",
        description="上市后20个交易日内显著下跌风险",
    )
    breaking_risk: Literal["high", "medium", "low"] = Field(
        default="medium",
        description="破发风险",
    )
    key_risk_factors: list[str] = Field(
        default_factory=list,
        description="导致下跌/破发的关键风险因素（来自各专家分析的综合结论）",
    )
    market_condition_assessment: str = Field(
        default="",
        description="当前市场窗口评估：发行窗口、市场情绪、流动性",
    )
    recommendation: str = Field(
        default="",
        exclude=True,
        description="投资者建议",
    )


class IntegratedRiskReport(StrictContract):
    title: str = Field(min_length=1)
    executive_summary: str = Field(min_length=1)
    overall_risk_level: str = Field(min_length=1)
    risk_score: float = Field(default=0.0, ge=0.0, le=100.0, exclude=True)
    risk_score_breakdown: dict[str, float] = Field(default_factory=dict, exclude=True)
    sections: list[IntegratedReportSection] = Field(default_factory=list)
    risk_themes: list[IntegratedRiskTheme] = Field(default_factory=list)
    cross_module_risks: list[IntegratedCrossModuleRisk] = Field(default_factory=list)
    cross_modal_analysis: CrossModalAnalysis | None = Field(
        default=None,
        description="跨模态共振分析：基本面与市场情绪交叉验证",
    )
    post_ipo_risk: PostIPORiskAssessment | None = Field(
        default=None,
        description="上市后风险预测评估：5日下跌风险、20日下跌风险与破发风险",
    )
    matrix_assessment: dict[str, Any] | None = Field(
        default=None,
        description="概率-影响矩阵聚合评估：overall_probability/impact、matrix_risk_level、risk_distribution、top_risks",
    )
    recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalise_risk_level(self) -> "IntegratedRiskReport":
        """Accept both legacy 3-level and new 5-level strings."""
        raw = self.overall_risk_level.strip().lower()
        if raw in {"high", "mid", "low", "very_high", "very_low", "medium", "unrated"}:
            mapped = map_legacy_risk_level(raw)
            self.overall_risk_level = mapped.value
        return self


# Compatibility aliases retained for callers during the API transition.
OrchestraStage = StrategyAssignment
OrchestraPlan = StrategyDecision


async def orchestrate(
    *,
    user_requirement: str,
    company_name: str,
    document_summary: str,
    available_agents: list[dict[str, Any]],
    recommended_agents: list[str] | None = None,
    skill_snapshot: dict[str, Any] | None = None,
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
            recommended_agents=recommended_agents,
            skill_snapshot=skill_snapshot,
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
    from riskagent.skill_docs import AGENT_FUNCTIONS, get_skill_catalog

    catalog = get_skill_catalog()
    for assignment in plan.assignments:
        function = AGENT_FUNCTIONS.get(assignment.agent_name)
        for skill_id in assignment.skill_ids:
            definition = catalog.definitions.get(skill_id)
            if definition is None or definition.document.execution == "offline":
                raise ValueError(f"Strategy assigned unavailable Skill: {skill_id}")
            owners = definition.document.owners
            if function and function not in owners and skill_id not in {
                "expert-risk-analysis",
                "evidence-verification",
            }:
                raise ValueError(
                    f"Agent {assignment.agent_name} cannot invoke Skill {skill_id}"
                )
    return plan


def manual_strategy_decision(
    *,
    agent_names: list[str],
    available_agents: list[dict[str, Any]],
    summary: str = "战略规划中断后由用户选择专家，系统依据 Agent Card 自动派题。",
) -> StrategyDecision:
    from riskagent.skill_docs import AGENT_SKILL_MAP, DEFAULT_EXPERT_SKILLS

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
                skill_ids=list(AGENT_SKILL_MAP.get(name, DEFAULT_EXPERT_SKILLS)),
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
    from riskagent.skill_docs import skill_ids_for_agent

    normalized_assignments = []
    for item in payload.get("assignments", []):
        normalized = dict(item)
        agent_name = _resolve_agent_name(str(item.get("agent_name", "")).strip(), roster)
        normalized["agent_name"] = agent_name
        if not normalized.get("skill_ids"):
            normalized["skill_ids"] = list(skill_ids_for_agent(agent_name))
        normalized_assignments.append(normalized)
    payload["assignments"] = normalized_assignments
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


# Mapping from severity string to probability (1-5) and impact (1-5)
_SEVERITY_TO_PROBABILITY: dict[str, int] = {
    "very_low": 1, "low": 2, "medium": 3, "high": 4, "very_high": 5,
}
_SEVERITY_TO_IMPACT: dict[str, int] = {
    "very_low": 1, "low": 2, "medium": 3, "high": 4, "very_high": 5,
}


def _enrich_findings_with_matrix(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure each finding has probability, impact and matrix_risk_level.

    If the finding already carries explicit probability/impact (e.g. from
    RuleEvaluation), those are preserved.  Otherwise we derive them from
    the severity field so that the matrix aggregation still works.
    """
    enriched: list[dict[str, Any]] = []
    for f in findings:
        item = dict(f)
        # Derive probability from severity when not explicitly set
        if "probability" not in item:
            sev = str(item.get("severity", "medium")).lower()
            item["probability"] = _SEVERITY_TO_PROBABILITY.get(sev, 3)
        if "impact" not in item:
            sev = str(item.get("severity", "medium")).lower()
            item["impact"] = _SEVERITY_TO_IMPACT.get(sev, 3)
        # Look up matrix risk level
        prob = max(1, min(5, int(item["probability"])))
        imp = max(1, min(5, int(item["impact"])))
        item["matrix_risk_level"] = RISK_MATRIX[prob][imp]
        enriched.append(item)
    return enriched


async def synthesize_report(
    *,
    company_name: str,
    data_cutoff_date: str,
    module_results: list[dict[str, Any]],
    verified_findings: list[dict[str, Any]],
    evidence_index: list[dict[str, Any]],
    validation_feedback: str | None = None,
    post_ipo_risk: dict[str, Any] | None = None,
) -> IntegratedRiskReport:
    raw = await get_default_llm_client().chat_json(
        _report_messages(
            company_name=company_name,
            data_cutoff_date=data_cutoff_date,
            module_results=module_results,
            verified_findings=verified_findings,
            evidence_index=evidence_index,
            validation_feedback=validation_feedback,
            post_ipo_risk=post_ipo_risk,
        ),
        temperature=0.35,
    )
    report = IntegratedRiskReport.model_validate(raw)

    # Compute quantitative risk score if not already provided by LLM
    if report.risk_score == 0.0 and verified_findings:
        report.risk_score = calculate_risk_score(verified_findings)
        # Derive risk level from score if LLM left it unrated
        if report.overall_risk_level == "unrated":
            report.overall_risk_level = score_to_risk_level(report.risk_score).value

    # Compute probability-impact matrix assessment if not provided by LLM
    if report.matrix_assessment is None and verified_findings:
        # Enrich each finding with probability/impact from severity mapping
        enriched = _enrich_findings_with_matrix(verified_findings)
        report.matrix_assessment = aggregate_risk_by_matrix(enriched)

    return report


def validate_report_references(
    report: IntegratedRiskReport,
    *,
    module_ids: set[str],
    finding_ids: set[str],
    finding_ids_by_module: Mapping[str, set[str]],
    evidence_by_finding: Mapping[str, set[str]],
    verification_status_by_finding: Mapping[str, str],
) -> None:
    unknown_modules = sorted({section.module_id for section in report.sections} - module_ids)
    if unknown_modules:
        raise ValueError(f"Integrated report contains unknown modules: {unknown_modules}")
    reference_sections = [
        *report.sections,
        *report.risk_themes,
        *report.cross_module_risks,
    ]
    referenced_findings = {
        finding_id
        for section in reference_sections
        for finding_id in section.finding_ids
    }
    referenced_evidence = {
        evidence_id
        for section in reference_sections
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
    for section in reference_sections:
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
    recommended_agents: list[str] | None = None,
    skill_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    roster = json.dumps(available_agents, ensure_ascii=False, sort_keys=True)
    template = render_orchestrator_prompt(
        "strategy_system",
        STRATEGY_SYSTEM_PROMPT,
        skill_snapshot=skill_snapshot,
    )
    # Build recommended agents hint for the strategy LLM
    if recommended_agents:
        hint = (
            "【系统预推荐】根据标的特征，系统预推荐以下专家作为编队基础（你可以调整，但请说明理由）："
            + "、".join(recommended_agents)
            + "。优先从此列表出发进行增减。"
        )
    else:
        hint = ""
    content = template.replace("{available_experts_json}", roster).replace(
        "{recommended_agents_hint}", hint
    )
    return [
        {"role": "system", "content": content},
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
    "你是 ProspectAlpha 战略层 Agent「沈北辰」。整合所有专家模块结果，输出供灵活报告合成层使用的内部结构化综合结果；"
    "不要生成 Markdown、章节标题或表格。"
    "不得新增输入中不存在的 finding_id 或 evidence_id；sections 与 risk_themes 数量由材料和风险逻辑自由决定，不要求模块全覆盖。"
    "每个被引用的 finding 必须在同一 section 中附带至少一条归属于它的 evidence；没有可验证引用时不得创建跨模块风险。"
    "module summary 已经过证据门禁清洗；不得依据没有 finding_id 的模块摘要宣称存在或不存在风险。"
    "module_results.screening_observations 是检索和二判审计记录，只能用于说明已核查范围、待核实线索和证据缺口，"
    "不得将其改写为正式风险结论；每个模块分析应具体说明核查数量、主要缺口和下一步补证方向，避免空泛套话。"
    "可按投研逻辑自由聚合 risk_themes，也可不聚合；不得为了固定数量拆分、遗漏或编造主题。"
    "低置信度、inferential 和 lead 发现允许呈现，confidence 仅供读者理解不确定性，不是报告准入门槛。"
    "每个主题必须给出明确结论、因果链分析、对盈利可见性/估值/流动性/经营兑现的具体影响、"
    "不确定性和可执行的持续观察指标；不得只复述规则、字段或原始数值。"
    "overall_risk_level 必须使用五级体系：very_low / low / medium / high / very_high，并由 verified_findings 推导；"
    "为空时只能输出 unrated，不得随意猜测。"
    "不得输出 0-100 综合风险分，也不得给出申购、买入、卖出等投资建议。"
    "\n"
    "【跨模态共振分析】你必须在 cross_modal_analysis 字段中完成以下分析："
    "1. consistency_assessment：判断基本面分析（财务/法务/业务发现）与市场情绪的一致性程度；"
    "2. resonance_risks：列出基本面风险与市场负面情绪共振的信号（基本面风险 + 市场冷淡 = 放大风险）；"
    "3. divergence_signals：列出基本面与市场情绪不一致所造成的判断不确定性和待补证事项；"
    "4. market_data_note：说明市场数据可用性，如市场数据不可用则注明'市场情绪数据不可用，跨模态分析基于有限信息'。"
    "\n"
    "【概率-影响矩阵】系统已为每个 verified_finding 计算 probability(1-5) 和 impact(1-5)，"
    "并查表得到 matrix_risk_level。请在评估 overall_risk_level 时参考 matrix_assessment.matrix_risk_level，"
    "将其与已核验证据覆盖情况综合判断最终风险等级。"
    "\n"
    "只输出 JSON："
    '{"title":str,"executive_summary":str,"overall_risk_level":"very_low"|"low"|"medium"|"high"|"very_high",'
    '"sections":[{"module_id":str,"title":str,"analysis":str,"finding_ids":[str],"evidence_ids":[str]}],'
    '"risk_themes":[{"title":str,"conclusion":str,"analysis":str,"investment_impact":str,'
    '"uncertainties":[str],"monitoring_points":[str],"finding_ids":[str],"evidence_ids":[str]}],'
    '"cross_module_risks":[{"title":str,"analysis":str,"finding_ids":[str],"evidence_ids":[str]}],'
    '"cross_modal_analysis":{"consistency_assessment":str,"resonance_risks":[str],'
    '"divergence_signals":[str],"market_data_note":str},'
    '"matrix_assessment":{"overall_probability":int,"overall_impact":int,"matrix_risk_level":str,'
    '"risk_distribution":{"very_high":int,"high":int,"medium":int,"low":int,"very_low":int},'
    '"top_risks":[{"probability":int,"impact":int,"risk_level":str,"description":str}]},'
    '"recommendations":[str],"limitations":[str]}。recommendations 仅允许写风险缓释与待补材料。'
)


def _report_messages(
    *,
    company_name: str,
    data_cutoff_date: str,
    module_results: list[dict[str, Any]],
    verified_findings: list[dict[str, Any]],
    evidence_index: list[dict[str, Any]],
    validation_feedback: str | None = None,
    post_ipo_risk: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    payload_dict = {
        "company_name": company_name,
        "data_cutoff_date": data_cutoff_date,
        "module_results": module_results,
        "verified_findings": verified_findings,
        "evidence_index": evidence_index,
    }
    # Pre-compute matrix assessment so the LLM can reference it
    if verified_findings:
        enriched = _enrich_findings_with_matrix(verified_findings)
        payload_dict["matrix_assessment"] = aggregate_risk_by_matrix(enriched)
    payload = json.dumps(payload_dict, ensure_ascii=False)
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
