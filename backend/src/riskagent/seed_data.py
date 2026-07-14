"""Idempotent bootstrap data for production reference tables."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from riskagent.models import AgentExpertRow, AgentSpecRow, RiskRuleDimensionRow, RiskRuleRow, RiskRuleSetRow


BACKEND_ROOT = Path(__file__).resolve().parents[2]
RISK_RULE_SEED_PATH = BACKEND_ROOT / "configs" / "hk_ipo_risk_rules_v1.json"

logger = logging.getLogger(__name__)


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
        "agent_name": "shen_beichen_orchestrator_agent",
        "persona_id": "L3-001",
        "display_name": "沈北辰",
        "layer": "L3",
        "function": "orchestrator",
        "title": "Strategic orchestrator - Shen Beichen",
        "rule_types": [],
        "categories": [],
        "is_default": False,
    },
    {
        "agent_name": "xiao_zeming_quality_agent",
        "persona_id": "L3-002",
        "display_name": "萧则明",
        "layer": "L3",
        "function": "quality",
        "title": "Quality arbiter - Xiao Zeming",
        "rule_types": [],
        "categories": [],
        "is_default": False,
    },
    {
        "agent_name": "zhou_shenyan_redteam_agent",
        "persona_id": "L3-003",
        "display_name": "周慎言",
        "layer": "L3",
        "function": "redteam",
        "title": "Red-team arbiter - Zhou Shenyan",
        "rule_types": [],
        "categories": [],
        "is_default": False,
    },
    {
        "agent_name": "su_zhiji_business_agent",
        "persona_id": "L2-001",
        "display_name": "苏知几",
        "layer": "L2",
        "function": "business_agent",
        "title": "Business specialist - Su Zhiji",
        "rule_types": [],
        "categories": [],
        "is_default": False,
    },
    {
        "agent_name": "jiang_hengzhi_market_agent",
        "persona_id": "L2-002",
        "display_name": "江衡之",
        "layer": "L2",
        "function": "market_agent",
        "title": "Market specialist - Jiang Hengzhi",
        "rule_types": [],
        "categories": ["行业竞争"],
        "is_default": False,
    },
    {
        "agent_name": "gu_lingsheng_market_agent",
        "persona_id": "L2-003",
        "display_name": "顾聆声",
        "layer": "L2",
        "function": "market_agent",
        "title": "Market specialist - Gu Lingsheng",
        "rule_types": [],
        "categories": ["行业竞争"],
        "is_default": False,
    },
    {
        "agent_name": "qin_bingwen_financial_agent",
        "persona_id": "L1-001",
        "display_name": "秦秉文",
        "layer": "L1",
        "function": "financial_agent",
        "title": "Financial specialist - Qin Bingwen",
        "rule_types": ["quantitative_financial"],
        "categories": [],
        "is_default": False,
    },
    {
        "agent_name": "lu_guanlan_market_agent",
        "persona_id": "L1-002",
        "display_name": "陆观澜",
        "layer": "L1",
        "function": "market_agent",
        "title": "Market specialist - Lu Guanlan",
        "rule_types": [],
        "categories": ["行业竞争"],
        "is_default": False,
    },
    {
        "agent_name": "wen_shouzheng_legal_agent",
        "persona_id": "L1-003",
        "display_name": "温守正",
        "layer": "L1",
        "function": "legal_agent",
        "title": "Legal specialist - Wen Shouzheng",
        "rule_types": [],
        "categories": ["关联交易", "对赌/赎回条款", "政策监管", "诉讼与纠纷", "担保与质押", "税务风险"],
        "is_default": False,
    },
    {
        "agent_name": "ye_zhiyuan_business_agent",
        "persona_id": "L1-004",
        "display_name": "叶致远",
        "layer": "L1",
        "function": "business_agent",
        "title": "Business specialist - Ye Zhiyuan",
        "rule_types": [],
        "categories": [],
        "is_default": False,
    },
    {
        "agent_name": "cheng_zheng_evidence_agent",
        "persona_id": "L1-005",
        "display_name": "程徵",
        "layer": "L1",
        "function": "evidence",
        "title": "Evidence retrieval specialist - Cheng Zheng",
        "rule_types": [],
        "categories": [],
        "is_default": False,
    },
    {
        "agent_name": "bai_shice_market_agent",
        "persona_id": "L1-006",
        "display_name": "白时策",
        "layer": "L1",
        "function": "market_agent",
        "title": "Market specialist - Bai Shice",
        "rule_types": [],
        "categories": ["行业竞争"],
        "is_default": False,
    },
]


AGENT_NAME_ALIASES: dict[str, str] = {
    "financial_agent": "qin_bingwen_financial_agent",
    "legal_agent": "wen_shouzheng_legal_agent",
    "market_agent": "lu_guanlan_market_agent",
    "business_agent": "su_zhiji_business_agent",
}


def seed_reference_data(session_factory: sessionmaker) -> None:
    with session_factory() as session:
        if not session.scalar(select(func.count()).select_from(AgentExpertRow)):
            for display_order, item in enumerate(AGENT_EXPERTS, start=1):
                session.add(AgentExpertRow(**item, display_order=display_order))

        _refresh_agent_specs_if_stale(session)

        if not session.scalar(select(func.count()).select_from(RiskRuleSetRow)):
            seed_risk_rules(session)
        else:
            verify_risk_rule_seed(session)

        session.commit()


def verify_risk_rule_seed(session) -> None:
    """Repair stale or partially seeded risk-rule data, idempotently.

    Re-seeds the bundled rule set only when the stored copy is unusable
    (missing rules/dimensions) or predates the current seed version.
    User CRUD on a fully seeded set — added, edited, disabled, or deleted
    rules — is left untouched; a count drift is only logged.
    """

    data = json.loads(RISK_RULE_SEED_PATH.read_text(encoding="utf-8"))
    rule_set_id = data["rule_set_id"]
    seed_version = data.get("version", "v1.0")
    seed_rule_count = len(data.get("rules", []))

    row = session.get(RiskRuleSetRow, rule_set_id)
    rule_count = session.scalar(
        select(func.count()).select_from(RiskRuleRow).where(RiskRuleRow.rule_set_id == rule_set_id)
    ) or 0
    dimension_count = session.scalar(
        select(func.count())
        .select_from(RiskRuleDimensionRow)
        .where(RiskRuleDimensionRow.rule_set_id == rule_set_id)
    ) or 0

    stale_version = row is not None and row.version != seed_version
    broken = row is None or rule_count == 0 or dimension_count == 0
    if not (stale_version or broken):
        if rule_count != seed_rule_count:
            logger.info(
                "risk rule set %s holds %d rules (seed has %d) — user-curated, keeping as is",
                rule_set_id,
                rule_count,
                seed_rule_count,
            )
        return

    logger.warning(
        "risk rule set %s is stale or incomplete (version=%s rules=%d dims=%d) — re-seeding %d rules",
        rule_set_id,
        row.version if row else None,
        rule_count,
        dimension_count,
        seed_rule_count,
    )
    session.execute(delete(RiskRuleRow).where(RiskRuleRow.rule_set_id == rule_set_id))
    session.execute(delete(RiskRuleDimensionRow).where(RiskRuleDimensionRow.rule_set_id == rule_set_id))
    session.execute(delete(RiskRuleSetRow).where(RiskRuleSetRow.rule_set_id == rule_set_id))
    session.flush()
    seed_risk_rules(session)


def _refresh_agent_specs_if_stale(session) -> None:
    """Ensure ``agent_specs`` matches the current ``AGENT_SPECS`` seed list.

    Pre-12-persona dev DBs either lack the ``persona_id`` column or still hold
    the legacy 4 generic rows. Detect either condition and rebuild the table
    contents from the seed so the runtime sees the full 12-persona catalog
    without requiring a manual migration.
    """

    bind = session.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns(AgentSpecRow.__tablename__)}
    if "persona_id" not in columns:
        session.execute(text(f"DROP TABLE IF EXISTS {AgentSpecRow.__tablename__}"))
        AgentSpecRow.__table__.create(bind=bind, checkfirst=True)
        session.commit()
    else:
        existing = session.scalar(select(func.count()).select_from(AgentSpecRow)) or 0
        if existing != len(AGENT_SPECS):
            session.execute(text(f"DELETE FROM {AgentSpecRow.__tablename__}"))

    if not session.scalar(select(func.count()).select_from(AgentSpecRow)):
        for display_order, item in enumerate(AGENT_SPECS, start=1):
            session.add(AgentSpecRow(**item, display_order=display_order))


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
