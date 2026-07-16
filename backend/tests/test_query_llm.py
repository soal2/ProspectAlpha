from __future__ import annotations

import asyncio
from pathlib import Path

from riskagent.agents import query_llm as query_llm_module
from riskagent.risk_rules import RiskRuleRepository


class _ScriptedLLM:
    def __init__(self):
        self.messages = []

    async def chat_json(self, messages, *, temperature=0.1):
        self.messages = messages
        return {
            "queries": [
                {"query": "候選藥物是否均未取得監管批准", "purpose": "研发管线依赖"},
                {"query": "概無重大訴訟 法律程序", "purpose": "反向证据"},
                {"query": "x", "purpose": "too short"},
            ]
        }


def test_expert_llm_rewrites_atomic_queries_from_assignment(monkeypatch):
    client = _ScriptedLLM()
    monkeypatch.setattr(query_llm_module, "get_default_llm_client", lambda: client)
    rules = RiskRuleRepository(
        Path(__file__).parents[1] / "configs" / "risk_rules.yaml"
    ).select_rules()[:2]

    queries = asyncio.run(
        query_llm_module.rewrite_retrieval_queries_with_llm(
            user_requirement="重点检查研发管线和诉讼",
            assignment={
                "module": "业务与法务",
                "goal": "核查研发管线依赖和重大诉讼",
                "expected_outputs": ["RiskFinding", "RunEvidence"],
            },
            rules=rules,
            seed_queries=["研发管线", "重大诉讼"],
        )
    )

    assert queries == ["候選藥物是否均未取得監管批准", "概無重大訴訟 法律程序"]
    assert "重点检查研发管线和诉讼" in client.messages[1]["content"]
