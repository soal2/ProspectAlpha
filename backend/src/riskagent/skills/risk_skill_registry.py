"""Domain skills for applying frozen HK IPO risk-rule snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from riskagent.risk_rules import RiskRule


@dataclass(frozen=True)
class DomainSkill:
    skill_id: str
    agent_name: str
    role_zh: str
    rule_types: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    evidence_requirement: str = "必须引用招股书原文块、页码、bbox 和 source_hash。"
    output_schema: str = "RiskFinding + RunEvidence"
    search_strategy: str = "使用规则标题、触发条件、来源章节与命中关键词检索 document_blocks。"


DOMAIN_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        skill_id="financial_risk_skill",
        agent_name="financial_agent",
        role_zh="财务风险专家",
        rule_types=("quantitative_financial",),
    ),
    DomainSkill(
        skill_id="legal_compliance_skill",
        agent_name="legal_agent",
        role_zh="法务合规专家",
        categories=("关联交易", "对赌/赎回条款", "政策监管", "诉讼与纠纷", "担保与质押", "税务风险"),
    ),
    DomainSkill(
        skill_id="market_valuation_skill",
        agent_name="market_agent",
        role_zh="估值与市场风险专家",
        categories=("行业竞争", "市场空间", "估值", "发行定价", "破发风险"),
    ),
    DomainSkill(
        skill_id="business_risk_skill",
        agent_name="business_agent",
        role_zh="业务风险专家",
        categories=("客户集中", "供应商集中", "持续经营", "业务模式", "研发", "存货", "应收账款"),
    ),
    DomainSkill(
        skill_id="evidence_audit_skill",
        agent_name="verifier",
        role_zh="证据审计专家",
        evidence_requirement="只校验证据充分性、去重与可追溯性，不创建新风险结论。",
        output_schema="VerificationResult",
    ),
    DomainSkill(
        skill_id="quality_review_skill",
        agent_name="quality_reviewer",
        role_zh="质检审裁专家",
        evidence_requirement="检查报告判断是否均有原文证据、页码和一致风险等级。",
        output_schema="QualityReviewResult",
    ),
)


def skills_for_agents(agent_names: list[str] | None = None) -> list[DomainSkill]:
    selected = {name for name in (agent_names or []) if name}
    return [skill for skill in DOMAIN_SKILLS if skill.agent_name in selected]


def skill_for_agent(agent_name: str) -> DomainSkill | None:
    return next((skill for skill in DOMAIN_SKILLS if skill.agent_name == agent_name), None)


def skill_for_rule(rule: RiskRule) -> DomainSkill:
    for skill in DOMAIN_SKILLS:
        if skill.agent_name in {"verifier", "quality_reviewer"}:
            continue
        if skill.rule_types and rule.rule_type in skill.rule_types:
            return skill
        if skill.categories and rule.category in skill.categories:
            return skill
    return next(skill for skill in DOMAIN_SKILLS if skill.skill_id == "business_risk_skill")


def rules_for_skill(
    skill: DomainSkill,
    rules: list[RiskRule],
    assigned_rule_ids: set[str] | None = None,
) -> list[RiskRule]:
    assigned = assigned_rule_ids or set()
    if skill.skill_id == "business_risk_skill":
        matched = [
            rule
            for rule in rules
            if rule.rule_id not in assigned and _matches_skill(skill, rule)
        ]
        if matched:
            return matched
        return [rule for rule in rules if rule.rule_id not in assigned]
    return [rule for rule in rules if _matches_skill(skill, rule)]


def rules_from_snapshot(snapshot: dict[str, Any]) -> list[RiskRule]:
    return [
        RiskRule.model_validate(rule)
        for rule in snapshot.get("rules", [])
        if rule.get("enabled", True)
    ]


def rule_search_query(rule: RiskRule, matched_terms: list[str] | None = None) -> str:
    parts = [
        rule.risk_item,
        rule.category,
        rule.trigger_condition,
        rule.source_sections,
        *(matched_terms or []),
    ]
    return " ".join(dict.fromkeys(part for part in parts if part))


def skill_payload(skill: DomainSkill, rule_count: int, rules: list[RiskRule]) -> dict[str, Any]:
    return {
        "skill_id": skill.skill_id,
        "agent_name": skill.agent_name,
        "role_zh": skill.role_zh,
        "rule_count": rule_count,
        "rule_filters": {
            "rule_types": list(skill.rule_types),
            "categories": list(skill.categories),
        },
        "sample_rule_ids": [rule.rule_id for rule in rules[:5]],
        "search_strategy": skill.search_strategy,
        "evidence_requirement": skill.evidence_requirement,
        "output_schema": skill.output_schema,
    }


def _matches_skill(skill: DomainSkill, rule: RiskRule) -> bool:
    if skill.rule_types and rule.rule_type in skill.rule_types:
        return True
    if skill.categories and rule.category in skill.categories:
        return True
    return False
