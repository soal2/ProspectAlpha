"""LLM query rewriting for expert-owned prospectus retrieval."""

from __future__ import annotations

import json
from typing import Any

from riskagent.providers.llm import get_default_llm_client
from riskagent.risk_rules import RiskRule


QUERY_REWRITE_SYSTEM_PROMPT = """\
你是 IPO 招股书检索 Query 规划器。请根据用户要求和专家模块任务，自主生成可直接用于
全文+向量混合检索的原子 Query。

要求：
1. 每条 Query 只核查一个事实，不要把多个风险条件拼成长句；
2. 优先使用招股书可能出现的繁体中文原词，并补充简体或英文术语；
3. 同时查找触发证据和否定性披露，例如“存在重大诉讼”与“概无重大诉讼”；
4. 数值规则要查询指标名、报告期、数值和变动原因；
5. 必须围绕 assignment.expected_outputs，不得扩展到无关模块；
6. 最多返回 10 条，每条 2-60 个字符。

只输出 JSON：{"queries":[{"query":str,"purpose":str}]}
"""


async def rewrite_retrieval_queries_with_llm(
    *,
    user_requirement: str,
    assignment: dict[str, Any],
    rules: list[RiskRule],
    seed_queries: list[str],
    limit: int = 10,
) -> list[str]:
    """Let the expert rewrite its own evidence questions, with validated output."""

    payload = {
        "user_requirement": user_requirement,
        "assignment": assignment,
        "priority_rules": [
            {
                "rule_id": rule.rule_id,
                "risk_item": rule.risk_item,
                "trigger_condition": rule.trigger_condition,
                "source_sections": rule.source_sections,
            }
            for rule in rules[:12]
        ],
        "deterministic_seed_queries": seed_queries[:14],
    }
    raw = await get_default_llm_client().chat_json(
        [
            {"role": "system", "content": QUERY_REWRITE_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.1,
    )
    items = raw.get("queries", []) if isinstance(raw, dict) else []
    queries: list[str] = []
    for item in items:
        value = item.get("query") if isinstance(item, dict) else item
        query = str(value or "").strip()
        if 2 <= len(query) <= 120 and query not in queries:
            queries.append(query)
    return queries[: max(1, limit)]
