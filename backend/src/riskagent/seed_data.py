"""Idempotent bootstrap data for production reference tables."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from riskagent.models import AgentExpertRow, AgentSpecRow, RiskRuleDimensionRow, RiskRuleRow, RiskRuleSetRow


BACKEND_ROOT = Path(__file__).resolve().parents[2]
RISK_RULE_SEED_PATH = BACKEND_ROOT / "configs" / "hk_ipo_risk_rules_v1.json"


AGENT_EXPERTS: list[dict[str, Any]] = [
    {
        "agent_id": "L3-001",
        "name": "沈北辰",
        "role": "首席指挥官 · 总监",
        "layer": "L3",
        "description": "统筹全局，拆解风险维度，组建专家队、终审报告并签发。",
        "expertise": ["策略", "总控", "报告"],
        "call_count": 312,
        "avg_tokens": 4820,
        "avatar": "沈",
        "accent": "#0b243d",
    },
    {
        "agent_id": "L3-002",
        "name": "萧则明",
        "role": "首席分析师",
        "layer": "L3",
        "description": "把控分析质量与逻辑严谨性，裁定结论是否成立。",
        "expertise": ["质检", "逻辑"],
        "call_count": 287,
        "avg_tokens": 3960,
        "avatar": "萧",
        "accent": "#173a5e",
    },
    {
        "agent_id": "L3-003",
        "name": "周慎言",
        "role": "质量总检 · 红队",
        "layer": "L3",
        "description": "扮演魔鬼代言人，主导事实校验与反馈闭环，决定是否打回。",
        "expertise": ["红队", "事实核查"],
        "call_count": 198,
        "avg_tokens": 3210,
        "avatar": "周",
        "accent": "#b42318",
    },
    {
        "agent_id": "L2-001",
        "name": "苏知几",
        "role": "风险策略顾问",
        "layer": "L2",
        "description": "从行业格局给出战略级判断与风险权重建议。",
        "expertise": ["策略", "行业"],
        "call_count": 156,
        "avg_tokens": 2850,
        "avatar": "苏",
        "accent": "#1e3a5f",
    },
    {
        "agent_id": "L2-002",
        "name": "江衡之",
        "role": "定价策略师",
        "layer": "L2",
        "description": "拆解发行定价模型、估值溢价与博弈，识别破发风险。",
        "expertise": ["估值", "定价"],
        "call_count": 142,
        "avg_tokens": 2640,
        "avatar": "江",
        "accent": "#c9a35b",
    },
    {
        "agent_id": "L2-003",
        "name": "顾聆声",
        "role": "用户研究 · 路演解读",
        "layer": "L2",
        "description": "构建投资者画像、解读路演问答、归纳市场口碑。",
        "expertise": ["路演", "舆情"],
        "call_count": 121,
        "avg_tokens": 2310,
        "avatar": "顾",
        "accent": "#7b8ba1",
    },
    {
        "agent_id": "L1-001",
        "name": "秦秉文",
        "role": "财报采集 · 财务分析",
        "layer": "L1",
        "description": "解析三大报表、毛利与现金流，识别盈利模式与造血能力。",
        "expertise": ["财务", "现金流"],
        "call_count": 412,
        "avg_tokens": 2120,
        "avatar": "秦",
        "accent": "#3b6fa0",
    },
    {
        "agent_id": "L1-002",
        "name": "陆观澜",
        "role": "市场情报 · 竞品",
        "layer": "L1",
        "description": "系统化追踪同业动态、市场份额、客户与供应商集中度。",
        "expertise": ["竞品", "市场"],
        "call_count": 388,
        "avg_tokens": 1980,
        "avatar": "陆",
        "accent": "#2c5f7a",
    },
    {
        "agent_id": "L1-003",
        "name": "温守正",
        "role": "法务合规 · 条款",
        "layer": "L1",
        "description": "拆解对赌、赎回、关联交易、特殊股东权利与诉讼条款。",
        "expertise": ["法务", "合规"],
        "call_count": 296,
        "avg_tokens": 2480,
        "avatar": "温",
        "accent": "#4a5a7a",
    },
    {
        "agent_id": "L1-004",
        "name": "叶致远",
        "role": "技术与管线",
        "layer": "L1",
        "description": "评估技术壁垒、研发投入、管线进展与商业化里程碑。",
        "expertise": ["研发", "管线"],
        "call_count": 218,
        "avg_tokens": 2280,
        "avatar": "叶",
        "accent": "#b54708",
    },
    {
        "agent_id": "L1-005",
        "name": "程徵",
        "role": "证据检索 · 文档",
        "layer": "L1",
        "description": "在招股书全文跨章节检索证据片段，定位页码与 bbox。",
        "expertise": ["检索", "RAG"],
        "call_count": 562,
        "avg_tokens": 1240,
        "avatar": "程",
        "accent": "#4a5568",
    },
    {
        "agent_id": "L1-006",
        "name": "白时策",
        "role": "市场情绪 · 行情",
        "layer": "L1",
        "description": "解读上市前市场情绪、板块流动性、近期新股表现与发行窗口。",
        "expertise": ["行情", "情绪"],
        "call_count": 247,
        "avg_tokens": 1860,
        "avatar": "白",
        "accent": "#8a6a3b",
    },
]


AGENT_SPECS: list[dict[str, Any]] = [
    {
        "agent_name": "financial_agent",
        "title": "Financial specialist",
        "rule_types": ["quantitative_financial"],
        "categories": [],
        "is_default": False,
    },
    {
        "agent_name": "legal_agent",
        "title": "Legal and compliance specialist",
        "rule_types": [],
        "categories": ["关联交易", "对赌/赎回条款", "政策监管", "诉讼与纠纷", "担保与质押", "税务风险"],
        "is_default": False,
    },
    {
        "agent_name": "market_agent",
        "title": "Market and industry specialist",
        "rule_types": [],
        "categories": ["行业竞争"],
        "is_default": False,
    },
    {
        "agent_name": "business_agent",
        "title": "Business and disclosure specialist",
        "rule_types": [],
        "categories": [],
        "is_default": True,
    },
]


def seed_reference_data(session_factory: sessionmaker) -> None:
    with session_factory() as session:
        if not session.scalar(select(func.count()).select_from(AgentExpertRow)):
            for display_order, item in enumerate(AGENT_EXPERTS, start=1):
                session.add(AgentExpertRow(**item, display_order=display_order))

        if not session.scalar(select(func.count()).select_from(AgentSpecRow)):
            for display_order, item in enumerate(AGENT_SPECS, start=1):
                session.add(AgentSpecRow(**item, display_order=display_order))

        if not session.scalar(select(func.count()).select_from(RiskRuleSetRow)):
            seed_risk_rules(session)

        session.commit()


def seed_risk_rules(session) -> None:
    data = json.loads(RISK_RULE_SEED_PATH.read_text(encoding="utf-8"))
    rule_set_id = data["rule_set_id"]
    session.add(
        RiskRuleSetRow(
            rule_set_id=rule_set_id,
            version=data.get("version", "v1.0"),
            name=data.get("name", "港股IPO招股书风险识别框架"),
            source_workbook=data.get("source_workbook"),
            source_main_sheet=data.get("source_main_sheet"),
            source_summary_sheet=data.get("source_summary_sheet"),
            scope=data.get("scope"),
            usage_notes=data.get("usage_notes", []),
            active=True,
            display_order=1,
        )
    )
    session.flush()
    for display_order, dimension in enumerate(data.get("dimensions", []), start=1):
        session.add(
            RiskRuleDimensionRow(
                dimension_id=f"{rule_set_id}:{dimension['dimension_code']}",
                rule_set_id=rule_set_id,
                dimension_code=dimension["dimension_code"],
                dimension=dimension["dimension"],
                rule_type=dimension["rule_type"],
                description=dimension.get("description"),
                display_order=display_order,
            )
        )
    for display_order, rule in enumerate(data.get("rules", []), start=1):
        session.add(
            RiskRuleRow(
                rule_id=rule["rule_id"],
                rule_set_id=rule_set_id,
                dimension_code=rule["dimension_code"],
                dimension=rule["dimension"],
                rule_type=rule["rule_type"],
                category=rule["category"],
                risk_item=rule["risk_item"],
                description=rule["description"],
                trigger_condition=rule["trigger_condition"],
                calculation_or_method=rule["calculation_or_method"],
                source_sections=rule["source_sections"],
                enabled=rule.get("enabled", True),
                display_order=display_order,
            )
        )
