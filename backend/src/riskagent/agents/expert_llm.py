"""LLM second-pass analyzer for rule-prefiltered risk candidates."""
from __future__ import annotations
import json
from collections.abc import Callable
from typing import Any

from riskagent.providers.llm import get_default_llm_client
from riskagent.skills.risk_extract import RuleEvaluation

EXPERT_SYSTEM_PROMPT = """\\
你是 ProspectAlpha 多 Agent 团队中的「{agent_title}」专家（agent_name={agent_name}）。
下面是你能力域内通过规则预筛的候选风险条目，以及对应招股书原文片段。
请逐条做严谨的二次判断，给出：
1. evaluation_result: triggered | not_triggered | insufficient_evidence
2. severity: low | medium | high
3. confidence: 0.0-1.0
4. reasoning_summary: 80-200 字，必须引用页码/章节或证据片段
5. needs_more_evidence: bool

只输出 JSON 对象，格式：{{"items":[{{"rule_id":str,"evaluation_result":str,"severity":str,"confidence":float,"reasoning_summary":str,"needs_more_evidence":bool}}]}}
"""

_ZERO_PRODUCT_REVENUE_TERMS = (
    "并无自产品销售产生的收益",
    "並無自產品銷售產生的收益",
    "尚无产品销售收入",
    "尚無產品銷售收入",
    "未自产品销售产生任何收益",
    "未自產品銷售產生任何收益",
)


async def analyze_findings_with_llm(
    *,
    agent_name: str,
    agent_title: str,
    candidates: list[RuleEvaluation],
    evidence_snippets: list[str],
    evidence_by_rule: dict[str, list[str]] | None = None,
    user_requirement: str = "",
    assignment: dict[str, Any] | None = None,
    query_history: list[str] | None = None,
    system_prompt: str | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_response: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """LLM second-pass on rule-prefiltered candidates; mutates candidates in-place."""
    if not candidates:
        return []
    system = system_prompt or EXPERT_SYSTEM_PROMPT.format(
        agent_title=agent_title, agent_name=agent_name
    )
    user_payload = json.dumps(
        {
            "user_requirement": user_requirement,
            "assignment": assignment or {},
            "query_history": query_history or [],
            "candidates": [c.model_dump() for c in candidates],
            "evidence_snippets": [s for s in evidence_snippets if s][:20],
            "evidence_by_rule": {
                rule_id: [snippet for snippet in snippets if snippet][:4]
                for rule_id, snippets in (evidence_by_rule or {}).items()
            },
        },
        ensure_ascii=False,
    )
    client = get_default_llm_client()
    if on_delta or on_response:
        raw, raw_response = await client.chat_json_stream(
            [{"role": "system", "content": system}, {"role": "user", "content": user_payload}],
            temperature=0.2,
            on_delta=on_delta,
        )
        if on_response:
            on_response(raw_response)
    else:
        raw = await client.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user_payload}],
            temperature=0.2,
        )
    items = raw.get("items", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    by_rule = {str(item.get("rule_id")): item for item in items if item.get("rule_id")}
    for cand in candidates:
        enriched = by_rule.get(cand.rule_id)
        if not enriched:
            cand.evaluation_result = "insufficient_evidence"
            cand.confidence = min(cand.confidence, 0.49)
            continue
        new_summary = enriched.get("reasoning_summary")
        if isinstance(new_summary, str) and new_summary.strip():
            cand.reasoning_summary = new_summary
        new_sev = enriched.get("severity")
        if new_sev in {"low", "medium", "high"}:
            cand.severity = new_sev
        try:
            cand.confidence = float(enriched.get("confidence", cand.confidence))
        except (TypeError, ValueError):
            pass
        needs_more = bool(enriched.get("needs_more_evidence", False))
        outcome = str(enriched.get("evaluation_result") or "")
        if outcome not in {"triggered", "not_triggered", "insufficient_evidence"}:
            # Fail closed when an older/malformed model reply omits the actual
            # trigger decision. Confidence alone cannot distinguish a
            # confirmed risk from a confident non-trigger.
            outcome = "insufficient_evidence"
        if needs_more:
            outcome = "insufficient_evidence"
        if outcome == "triggered" and _invalid_zero_revenue_share(cand):
            outcome = "insufficient_evidence"
            cand.confidence = min(cand.confidence, 0.49)
            cand.reasoning_summary = (
                f"{cand.reasoning_summary.rstrip()} 原文明确尚无产品销售收入，"
                "无法计算要求非零收入分母的产品收入占比，因此不得判定该阈值已触发。"
            )
        cand.evaluation_result = outcome
    return items


def _invalid_zero_revenue_share(candidate: RuleEvaluation) -> bool:
    """Reject the invalid inference that dependence means 100% of zero revenue."""

    trigger = candidate.trigger_condition
    if "收入占比" not in trigger or ">" not in trigger:
        return False
    evidence = " ".join([candidate.reasoning_summary, *candidate.evidence_snippets])
    return any(term in evidence for term in _ZERO_PRODUCT_REVENUE_TERMS)
