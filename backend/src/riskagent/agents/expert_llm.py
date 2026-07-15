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
1. severity: low | medium | high
2. confidence: 0.0-1.0
3. reasoning_summary: 80-200 字，必须引用页码/章节或证据片段
4. needs_more_evidence: bool

只输出 JSON 对象，格式：{{"items":[{{"rule_id":str,"severity":str,"confidence":float,"reasoning_summary":str,"needs_more_evidence":bool}}]}}
"""


async def analyze_findings_with_llm(
    *,
    agent_name: str,
    agent_title: str,
    candidates: list[RuleEvaluation],
    evidence_snippets: list[str],
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
            "candidates": [c.model_dump() for c in candidates],
            "evidence_snippets": [s for s in evidence_snippets if s][:20],
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
        cand.evaluation_result = "triggered"
    return items
