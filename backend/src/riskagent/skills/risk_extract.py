"""Rule-driven risk extraction skill boundary."""

from __future__ import annotations

from pydantic import BaseModel

from riskagent.risk_rules import RiskRule, RiskRuleRepository, get_default_repository


class RuleEvaluation(BaseModel):
    rule_set_id: str
    rule_version: str
    rule_id: str
    dimension_code: str
    rule_type: str
    category: str
    title: str
    trigger_condition: str
    evaluation_result: str
    reasoning_summary: str
    matched_terms: list[str]
    evidence_snippets: list[str] = []
    source_type: str = "document_text"
    skill_id: str | None = None
    severity: str = "medium"
    confidence: float = 0.5


def load_rules_for_agent(
    *,
    dimension_code: str | None = None,
    rule_type: str | None = None,
    category: str | None = None,
    repository: RiskRuleRepository | None = None,
) -> list[RiskRule]:
    repo = repository or get_default_repository()
    return repo.select_rules(dimension_code=dimension_code, rule_type=rule_type, category=category)


def evaluate_text_against_rules(
    text: str,
    *,
    rules: list[RiskRule] | None = None,
    repository: RiskRuleRepository | None = None,
    rule_set_snapshot: dict | None = None,
) -> list[RuleEvaluation]:
    repo = repository or get_default_repository()
    rule_set = rule_set_snapshot or repo.load().model_dump()
    active_rules = rules or repo.select_rules()
    normalized_text = text.lower()
    findings: list[RuleEvaluation] = []
    for rule in active_rules:
        terms = _terms_for_rule(rule)
        matched = [term for term in terms if term.lower() in normalized_text]
        result = "triggered" if matched else "not_evaluated"
        summary = (
            f"文本命中规则关键词：{', '.join(matched)}"
            if matched
            else "未在当前文本片段中命中规则关键词，需结合结构化抽取或人工复核。"
        )
        findings.append(
            RuleEvaluation(
                rule_set_id=str(rule_set["rule_set_id"]),
                rule_version=str(rule_set["version"]),
                rule_id=rule.rule_id,
                dimension_code=rule.dimension_code,
                rule_type=rule.rule_type,
                category=rule.category,
                title=rule.risk_item,
                trigger_condition=rule.trigger_condition,
                evaluation_result=result,
                reasoning_summary=summary,
                matched_terms=matched,
                evidence_snippets=_snippets_for_terms(text, matched),
            )
        )
    return findings


def _terms_for_rule(rule: RiskRule) -> list[str]:
    terms = [rule.risk_item, rule.category]
    aliases = {
        "对赌": ["valuation adjustment", "vAM", "ratchet"],
        "赎回": ["redemption"],
        "回购": ["repurchase", "buyback"],
        "关联交易": ["related party transaction", "connected transaction"],
        "现金流": ["cash flow"],
        "客户": ["customer"],
        "供应商": ["supplier"],
        "诉讼": ["litigation", "lawsuit"],
        "纠纷": ["dispute"],
        "担保": ["guarantee"],
        "质押": ["pledge"],
        "持续经营": ["going concern"],
        "知识产权": ["intellectual property", "patent"],
        "税务": ["tax"],
        "监管": ["regulatory", "regulation"],
    }
    for token in [
        "对赌",
        "赎回",
        "回购",
        "关联交易",
        "现金流",
        "客户",
        "供应商",
        "诉讼",
        "纠纷",
        "担保",
        "质押",
        "持续经营",
        "知识产权",
        "税务",
        "监管",
        "免责声明",
        "前瞻性陈述",
        "模糊",
        "重大",
        "显著",
        "毛利率",
        "净利率",
        "资产负债率",
        "存货",
        "应收账款",
    ]:
        if token in f"{rule.risk_item} {rule.description} {rule.trigger_condition}":
            terms.append(token)
            terms.extend(aliases.get(token, []))
    return list(dict.fromkeys(term for term in terms if term))


def _snippets_for_terms(text: str, terms: list[str], *, limit: int = 2, radius: int = 70) -> list[str]:
    snippets: list[str] = []
    lower_text = text.lower()
    for term in terms:
        idx = lower_text.find(term.lower())
        if idx < 0:
            continue
        start = max(0, idx - radius)
        end = min(len(text), idx + len(term) + radius)
        snippet = text[start:end].replace("\n", " ").strip()
        if snippet and snippet not in snippets:
            snippets.append(snippet)
        if len(snippets) >= limit:
            break
    return snippets
