"""Rule-aware query rewriting and lightweight evidence reranking.

The PostgreSQL hybrid score is a ranking signal, not a calibrated probability.
This module keeps that raw score intact and derives a separate relevance score
from rule anchors and prospectus section hints.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from riskagent.risk_rules import RiskRule


GENERIC_TRIGGER_TERMS = frozenset(
    {"重大", "显著", "可能", "风险", "异常", "模糊", "下降", "不足", "偏低", "过高"}
)
_SPLIT_RE = re.compile(r"[\s/、，,；;：:+（）()<>×|]+")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?|连续[二三四五六七八九两\d]+期|报告期")
_DOMAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "持续经营": ("持續經營", "going concern"),
    "重大不确定性": ("重大不確定性", "material uncertainty"),
    "审计": ("審計", "核數師", "auditor"),
    "财务资料": ("財務資料", "financial information"),
    "风险因素": ("風險因素", "risk factors"),
    "客户": ("客戶", "customer"),
    "供应商": ("供應商", "supplier"),
    "关联交易": ("關聯交易", "關連交易", "connected transaction", "related party transaction"),
    "关联方": ("關聯方", "關連方", "related party"),
    "现金流": ("現金流", "cash flow"),
    "经营活动": ("經營活動", "operating activities"),
    "收入": ("收益", "營業收入", "revenue"),
    "占比": ("佔比", "百分比", "percentage"),
    "金额": ("金額", "amount"),
    "诉讼": ("訴訟", "litigation"),
    "监管": ("監管", "regulatory"),
    "毛利率": ("gross margin",),
    "净利率": ("淨利率", "net margin"),
}
_WEAK_ANCHORS = frozenset(
    {
        "收入", "收益", "revenue", "金额", "金額", "amount", "占比", "佔比",
        "百分比", "percentage", "审计", "審計", "核數師", "auditor",
        "客户", "客戶", "customer", "供应商", "供應商", "supplier",
        "营业收入", "營業收入",
    }
)


def rule_anchor_terms(rule: RiskRule) -> list[str]:
    """Return domain anchors while excluding generic risk adjectives."""

    source = f"{rule.risk_item} {rule.category} {rule.trigger_condition}"
    candidates = [rule.risk_item, rule.category, *_SPLIT_RE.split(source)]
    anchors: list[str] = []
    for term in candidates:
        value = term.strip("-—_ ")
        if len(value) < 2 or value in GENERIC_TRIGGER_TERMS or _NUMBER_RE.fullmatch(value):
            continue
        if value not in anchors:
            anchors.append(value)
    descriptor = f"{rule.risk_item} {rule.category} {rule.trigger_condition}"
    for token, aliases in _DOMAIN_ALIASES.items():
        if token in descriptor:
            for alias in (token, *aliases):
                if alias not in anchors:
                    anchors.append(alias)
    return anchors


def build_rule_search_queries(
    rule: RiskRule,
    *,
    user_requirement: str = "",
) -> list[str]:
    """Rewrite a rule into complementary disclosure, fact and counter queries."""

    sections = rule.source_sections.strip()
    aliases = _query_aliases(rule)
    focus = _specific_user_focus(user_requirement)
    queries = _compact_query_variants(rule)
    queries.extend([
        (
            f"核查招股书中的{rule.risk_item}。触发条件：{rule.trigger_condition}。"
            f"重点章节：{sections}。请查找报告期数据、事实和管理层解释。{aliases}"
        ),
        (
            f"{rule.category} {rule.risk_item} 报告期 数值 变动趋势 原因 "
            f"{sections} {aliases}"
        ),
        (
            f"{rule.risk_item} 是否存在 不存在 已改善 未发生 反向证据 "
            f"风险因素 {sections} {aliases}"
        ),
    ])
    if focus:
        queries.insert(0, f"{focus}；重点核查{rule.risk_item}，条件为{rule.trigger_condition}")
    return list(dict.fromkeys(query.strip() for query in queries if query.strip()))[:6]


def build_module_search_queries(
    *,
    module: str,
    goal: str,
    rules: Iterable[RiskRule],
    user_requirement: str = "",
    limit: int = 14,
) -> list[str]:
    """Create category-scoped queries instead of one broad module query."""

    grouped: dict[str, list[RiskRule]] = {}
    for rule in rules:
        grouped.setdefault(rule.category, []).append(rule)
    queries: list[str] = []
    focus = _specific_user_focus(user_requirement)
    for category, category_rules in grouped.items():
        items = "；".join(rule.risk_item for rule in category_rules[:5])
        conditions = "；".join(rule.trigger_condition for rule in category_rules[:3])
        sections = "；".join(
            dict.fromkeys(rule.source_sections for rule in category_rules[:5] if rule.source_sections)
        )
        queries.append(
            f"{module}。核查{category}：{items}。判断条件：{conditions}。"
            f"目标章节：{sections}。查找报告期原文、数值及反向证据。"
        )
    if not queries:
        queries.append(f"{module} {goal} {focus}".strip())
    return queries[: max(1, limit)]


def rerank_rule_candidates(
    candidates: Iterable[dict[str, Any]],
    *,
    rule: RiskRule,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Gate and rerank retrieved chunks without treating hybrid score as confidence."""

    anchors = rule_anchor_terms(rule)
    specific_anchors = _specific_rule_anchors(rule)
    section_terms = [
        term for term in _SPLIT_RE.split(rule.source_sections) if len(term.strip()) >= 2
    ]
    ranked: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for candidate in candidates:
        key = (str(candidate.get("block_id") or ""), int(candidate.get("page_number") or 0))
        if key in seen:
            continue
        seen.add(key)
        text = str(candidate.get("text") or "").lower()
        section = str(candidate.get("section_path") or "").lower()
        matched_anchors = [anchor for anchor in anchors if anchor.lower() in text]
        specific_matches = [anchor for anchor in specific_anchors if anchor.lower() in text]
        section_match = any(term.lower() in section or term.lower() in text[:160] for term in section_terms)
        raw_score = float(candidate.get("retrieval_score", candidate.get("score", 0.0)) or 0.0)
        # Vector ranking scores around 0.2-0.4 are normal. Admission therefore
        # requires a rule/section anchor, not an arbitrary probability cutoff.
        strong_phrase = any(len(anchor) >= 3 for anchor in specific_matches)
        if not strong_phrase and len(set(specific_matches)) < 2:
            continue
        semantic_component = min(0.35, max(0.0, raw_score) * 1.5)
        lexical = min(0.45, 0.35 + 0.08 * max(0, len(set(specific_matches)) - 1))
        section_bonus = 0.12 if section_match else 0.0
        numeric_bonus = 0.08 if _NUMBER_RE.search(rule.trigger_condition) and re.search(r"\d", text) else 0.0
        relevance = min(1.0, semantic_component + lexical + section_bonus + numeric_bonus)
        enriched = dict(candidate)
        enriched["retrieval_score"] = raw_score
        enriched["relevance_score"] = relevance
        enriched["matched_anchors"] = matched_anchors
        enriched["section_match"] = section_match
        enriched["score"] = relevance
        ranked.append(enriched)
    ranked.sort(
        key=lambda item: (
            float(item["relevance_score"]),
            float(item["retrieval_score"]),
        ),
        reverse=True,
    )
    return ranked[:limit]


def _specific_user_focus(user_requirement: str) -> str:
    value = " ".join(user_requirement.split()).strip()
    if not value:
        return ""
    residue = value
    for token in ("请", "根据", "我", "上传", "的", "PDF", "招股书", "企业", "公司", "对", "该", "所示", "进行", "IPO", "上市", "风险", "全面", "全量", "审核", "分析"):
        residue = residue.replace(token, "")
    residue = residue.strip("，。；;:： ")
    return value[:240] if len(residue) >= 4 else ""


def _query_aliases(rule: RiskRule) -> str:
    descriptor = f"{rule.risk_item} {rule.category} {rule.trigger_condition} {rule.source_sections}"
    values: list[str] = []
    for token, aliases in _DOMAIN_ALIASES.items():
        if token in descriptor:
            values.extend(aliases)
    return " ".join(dict.fromkeys(values))


def _specific_rule_anchors(rule: RiskRule) -> list[str]:
    descriptor = f"{rule.risk_item} {rule.trigger_condition}"
    values = [rule.risk_item, *_SPLIT_RE.split(descriptor)]
    for token, aliases in _DOMAIN_ALIASES.items():
        if token in descriptor:
            values.extend((token, *aliases))
    return list(
        dict.fromkeys(
            value.strip()
            for value in values
            if len(value.strip()) >= 2
            and value.strip() not in GENERIC_TRIGGER_TERMS
            and value.strip() not in _WEAK_ANCHORS
            and not _NUMBER_RE.fullmatch(value.strip())
        )
    )


def _compact_query_variants(rule: RiskRule) -> list[str]:
    descriptor = f"{rule.risk_item} {rule.category} {rule.trigger_condition}"
    if "持续经营" in descriptor:
        return [
            "持續經營",
            "重大不確定性",
            "going concern",
        ]
    if "客户" in descriptor:
        return [
            "五大客戶",
            "主要客戶",
            "customer concentration",
        ]
    if "关联交易" in descriptor or "关联方" in descriptor:
        return [
            "關聯方交易",
            "關連交易",
            "related party transactions",
        ]
    aliases = _query_aliases(rule)
    return [f"{rule.risk_item} {rule.category} {aliases}".strip()]
