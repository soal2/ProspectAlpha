"""Rule-aware query rewriting and lightweight evidence reranking.

The PostgreSQL hybrid score is a ranking signal, not a calibrated probability.
This module keeps that raw score intact and derives a separate relevance score
from rule anchors and prospectus section hints.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

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
_USER_CONCEPT_QUERIES: dict[str, tuple[str, ...]] = {
    "股票代号": ("股份代號", "股份代号", "stock code"),
    "股份代号": ("股份代號", "股份代号", "stock code"),
    "发行价": ("發售價", "发售价", "offer price"),
    "发售价": ("發售價", "发售价", "offer price"),
    "发行定价": ("發售價 每股發售股份", "发行定价 估值", "offer price"),
    "估值": ("估值", "市盈率", "市销率", "valuation"),
    "破发": ("發售價 每股發售股份", "上市后股价风险", "市场风险"),
    "现金流": ("經營活動所得現金淨額", "经营活动现金流净额", "cash flow"),
    "客户集中": ("五大客戶", "主要客戶", "customer concentration"),
    "供应商集中": ("五大供應商", "主要供應商", "supplier concentration"),
    "研发管线": (
        "候選藥物 臨床試驗 未能獲批 商品化",
        "候选药物 商业化 监管批准",
        "核心產品 研發管線 依賴",
    ),
    "管线依赖": (
        "候選藥物 臨床試驗 未能獲批 商品化",
        "目前均未取得監管批准",
        "業務非常依賴 候選藥物",
    ),
    "持续经营": ("持續經營 重大不確定性", "连续亏损 现金储备", "going concern"),
    "业务模式": ("業務模式 收益來源 商業化", "产品销售收入", "business model"),
    "关联交易": (
        "關連交易 金額 定價 政策",
        "關聯方交易 應收 應付",
        "related party transactions",
    ),
    "对赌": ("特殊股東權利 對賭 贖回", "可贖回優先股 特殊權利", "redemption rights"),
    "赎回": ("可贖回優先股 贖回權", "特殊股東權利", "redemption"),
    "诉讼": (
        "概無重大訴訟 法律程序 處罰",
        "並無面臨任何法律訴訟",
        "未決或威脅 法律程序",
    ),
    "政策监管": ("監管合規 行政處罰 牌照", "政策法規 重大不利變化", "regulatory"),
    "税务": ("稅務爭議 稽查 處罰", "實際稅率 法定稅率", "tax dispute"),
    "行业竞争": (
        "競爭對手 市場競爭 候選藥物",
        "業務 競爭 主要製藥公司",
        "competitors market competition",
    ),
    "市场空间": ("市場規模 患者人數 增長率", "目標患者人群 市場接納", "market size CAGR"),
}

_CONCEPT_RULE_HINTS: dict[str, tuple[str, ...]] = {
    "现金流": ("现金流", "現金流"),
    "客户集中": ("客户", "客戶", "客户/供应商依赖"),
    "供应商集中": ("供应商", "供應商", "客户/供应商依赖"),
    "研发管线": ("管线", "核心产品", "临床", "核心技术", "持续经营"),
    "管线依赖": ("管线", "核心产品", "临床", "核心技术", "持续经营"),
    "持续经营": ("持续经营", "亏损", "现金流"),
    "业务模式": ("业务模式", "持续经营", "核心产品"),
    "关联交易": ("关联交易", "關連交易", "关联方"),
    "对赌": ("对赌", "赎回", "特殊股东权利"),
    "赎回": ("赎回", "对赌", "特殊股东权利"),
    "诉讼": ("诉讼", "纠纷", "处罚"),
    "政策监管": ("政策监管", "行业牌照", "行政处罚", "合规"),
    "税务": ("税务", "税率", "转让定价"),
    "行业竞争": ("行业竞争", "技术替代"),
    "市场空间": ("市场空间", "行业竞争"),
    "发行定价": ("发行定价", "估值", "发售价"),
    "估值": ("估值", "发行定价"),
    "破发": ("破发", "发行定价", "市场风险"),
}


class RetrievalIntent(BaseModel):
    """One atomic evidence question and its complementary short searches."""

    question: str
    queries: list[str] = Field(min_length=1)
    section_hints: list[str] = Field(default_factory=list)
    required_facts: list[str] = Field(default_factory=list)
    source: str = "assignment"


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
    expected_outputs: Iterable[str] = (),
    limit: int = 14,
) -> list[str]:
    """Flatten atomic retrieval intents into short hybrid-search queries."""

    intents = build_module_search_intents(
        module=module,
        goal=goal,
        rules=rules,
        user_requirement=user_requirement,
        expected_outputs=expected_outputs,
        limit=limit,
    )
    queries = _round_robin_queries(intents)
    return list(dict.fromkeys(query.strip() for query in queries if query.strip()))[: max(1, limit)]


def build_module_search_intents(
    *,
    module: str,
    goal: str,
    rules: Iterable[RiskRule],
    user_requirement: str = "",
    expected_outputs: Iterable[str] = (),
    limit: int = 14,
) -> list[RetrievalIntent]:
    """Translate user/assignment/rules into auditable, atomic evidence needs."""

    context = " ".join(
        [user_requirement, module, goal, *[str(item) for item in expected_outputs]]
    )
    active_rules = prioritize_rules_for_context(list(rules), context=context)
    intents: list[RetrievalIntent] = []
    focus = _specific_user_focus(user_requirement)
    if focus:
        concept_queries = _concept_queries(focus)
        intents.append(
            RetrievalIntent(
                question=focus,
                queries=concept_queries or [focus[:120]],
                required_facts=_matched_concepts(focus),
                source="user_requirement",
            )
        )

    assignment_text = " ".join([goal, *[str(item) for item in expected_outputs]])
    for concept in _matched_concepts(assignment_text):
        variants = list(_USER_CONCEPT_QUERIES[concept])
        intents.append(
            RetrievalIntent(
                question=f"{module}需要核查：{concept}",
                queries=variants,
                required_facts=[concept],
                source="assignment",
            )
        )

    for rule in _interleave_rules_by_category(active_rules):
        section_hints = [
            item.strip() for item in _SPLIT_RE.split(rule.source_sections) if item.strip()
        ][:4]
        compact = _compact_query_variants(rule)
        queries = [rule.risk_item, *compact]
        if section_hints:
            queries.append(f"{rule.risk_item} {section_hints[0]}")
        numeric_tokens = _NUMBER_RE.findall(rule.trigger_condition)
        if numeric_tokens:
            queries.append(f"{rule.risk_item} {' '.join(numeric_tokens[:2])}")
        intents.append(
            RetrievalIntent(
                question=f"核查{rule.risk_item}是否达到条件：{rule.trigger_condition}",
                queries=list(dict.fromkeys(query for query in queries if query)),
                section_hints=section_hints,
                required_facts=[rule.risk_item, *numeric_tokens[:2]],
                source=f"rule:{rule.rule_id}",
            )
        )

    if not intents:
        fallback = " ".join(part for part in (module, goal) if part).strip()
        intents.append(
            RetrievalIntent(
                question=fallback,
                queries=[fallback[:120]],
                source="fallback",
            )
        )
    deduped: list[RetrievalIntent] = []
    seen: set[str] = set()
    for intent in intents:
        key = "|".join(intent.queries)
        if key not in seen:
            seen.add(key)
            deduped.append(intent)
    return deduped[: max(1, limit)]


def build_evidence_gap_queries(
    rule: RiskRule,
    *,
    missing_reason: str = "",
    user_requirement: str = "",
    module_goal: str = "",
    limit: int = 6,
) -> list[str]:
    """Rewrite a failed evidence need into narrower exact/fact/counter searches."""

    queries = [
        *_compact_query_variants(rule),
        rule.risk_item,
        f"{rule.risk_item} 原因 改善措施",
    ]
    queries.extend(_concept_queries(f"{user_requirement} {module_goal} {missing_reason}"))
    for section in [item.strip() for item in _SPLIT_RE.split(rule.source_sections) if item.strip()][:3]:
        queries.append(f"{rule.risk_item} {section}")
    return list(dict.fromkeys(query.strip() for query in queries if query.strip()))[:limit]


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


def _matched_concepts(text: str) -> list[str]:
    return [concept for concept in _USER_CONCEPT_QUERIES if concept in text]


def prioritize_rules_for_context(
    rules: list[RiskRule],
    *,
    context: str,
) -> list[RiskRule]:
    """Put rules answering the assignment's concrete evidence needs first."""

    concepts = _matched_concepts(context)

    def score(rule: RiskRule) -> int:
        descriptor = " ".join(
            [rule.category, rule.risk_item, rule.trigger_condition, rule.source_sections]
        )
        return sum(
            10
            for concept in concepts
            if any(hint in descriptor for hint in _CONCEPT_RULE_HINTS.get(concept, (concept,)))
        )

    return [
        rule
        for _, rule in sorted(
            enumerate(rules),
            key=lambda pair: (-score(pair[1]), pair[0]),
        )
    ]


def _concept_queries(text: str) -> list[str]:
    queries: list[str] = []
    for concept in _matched_concepts(text):
        queries.extend(_USER_CONCEPT_QUERIES[concept])
    return list(dict.fromkeys(queries))


def _round_robin_queries(intents: list[RetrievalIntent]) -> list[str]:
    queries: list[str] = []
    width = max((len(intent.queries) for intent in intents), default=0)
    for index in range(width):
        for intent in intents:
            if index < len(intent.queries):
                queries.append(intent.queries[index])
    return queries


def _interleave_rules_by_category(rules: list[RiskRule]) -> list[RiskRule]:
    grouped: dict[str, list[RiskRule]] = {}
    for rule in rules:
        grouped.setdefault(rule.category, []).append(rule)
    ordered: list[RiskRule] = []
    width = max((len(items) for items in grouped.values()), default=0)
    for index in range(width):
        for items in grouped.values():
            if index < len(items):
                ordered.append(items[index])
    return ordered


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
