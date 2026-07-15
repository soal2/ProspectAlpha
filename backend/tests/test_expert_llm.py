from __future__ import annotations

import asyncio
from typing import Any

import pytest

from riskagent.agents import expert_llm as expert_llm_module
from riskagent.skills.risk_extract import RuleEvaluation


class _ScriptedLLM:
    def __init__(self, response: Any):
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    async def chat_json(self, messages, *, temperature: float = 0.2):
        self.calls.append(messages)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _candidate(rule_id: str) -> RuleEvaluation:
    return RuleEvaluation(
        rule_set_id="rs-1",
        rule_version="v1",
        rule_id=rule_id,
        dimension_code="dim-1",
        rule_type="rule",
        category="cat",
        title="title",
        trigger_condition="condition",
        evaluation_result="pending",
        reasoning_summary="原始摘要",
        matched_terms=["term"],
        evidence_snippets=["p.1 章节"],
    )


def test_analyze_findings_with_llm_empty_candidates_returns_empty_list(monkeypatch):
    llm = _ScriptedLLM({"items": []})
    monkeypatch.setattr(expert_llm_module, "get_default_llm_client", lambda: llm)

    result = asyncio.run(
        expert_llm_module.analyze_findings_with_llm(
            agent_name="agent_name",
            agent_title="agent_title",
            candidates=[],
            evidence_snippets=["snippet"],
        )
    )

    assert result == []
    assert llm.calls == []


def test_analyze_findings_with_llm_enriches_candidate_fields(monkeypatch):
    llm = _ScriptedLLM(
        {
            "items": [
                {
                    "rule_id": "R1",
                    "severity": "high",
                    "confidence": 0.9,
                    "reasoning_summary": "第 12 页第 3 节显示现金流压力，证据片段已覆盖。",
                    "needs_more_evidence": False,
                }
            ]
        }
    )
    monkeypatch.setattr(expert_llm_module, "get_default_llm_client", lambda: llm)
    candidate = _candidate("R1")

    result = asyncio.run(
        expert_llm_module.analyze_findings_with_llm(
            agent_name="agent_name",
            agent_title="agent_title",
            candidates=[candidate],
            evidence_snippets=["snippet-1", ""],
        )
    )

    assert result == llm.response["items"]
    assert candidate.reasoning_summary == "第 12 页第 3 节显示现金流压力，证据片段已覆盖。"
    assert candidate.severity == "high"
    assert candidate.confidence == 0.9
    assert candidate.evaluation_result == "triggered"


def test_analyze_findings_with_llm_propagates_llm_errors(monkeypatch):
    llm = _ScriptedLLM(RuntimeError("boom"))
    monkeypatch.setattr(expert_llm_module, "get_default_llm_client", lambda: llm)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(
            expert_llm_module.analyze_findings_with_llm(
                agent_name="agent_name",
                agent_title="agent_title",
                candidates=[_candidate("R1")],
                evidence_snippets=["snippet"],
            )
        )
