from __future__ import annotations

import asyncio
import json
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
                    "evaluation_result": "triggered",
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
    payload = json.loads(llm.calls[0][1]["content"])
    assert payload["evidence_by_rule"] == {}


def test_analyze_findings_with_llm_sends_rule_scoped_evidence(monkeypatch):
    llm = _ScriptedLLM({"items": []})
    monkeypatch.setattr(expert_llm_module, "get_default_llm_client", lambda: llm)

    asyncio.run(
        expert_llm_module.analyze_findings_with_llm(
            agent_name="agent_name",
            agent_title="agent_title",
            candidates=[_candidate("R1")],
            evidence_snippets=["global"],
            evidence_by_rule={"R1": ["第 424 页关联交易", "第 576 页关联方表格"]},
        )
    )

    payload = json.loads(llm.calls[0][1]["content"])
    assert payload["evidence_by_rule"]["R1"] == [
        "第 424 页关联交易",
        "第 576 页关联方表格",
    ]


def test_analyze_findings_with_llm_preserves_confirmed_non_trigger(monkeypatch):
    llm = _ScriptedLLM(
        {
            "items": [
                {
                    "rule_id": "R1",
                    "evaluation_result": "not_triggered",
                    "severity": "low",
                    "confidence": 0.92,
                    "reasoning_summary": "第 12 页明确披露不存在该安排，触发条件不成立。",
                    "needs_more_evidence": False,
                }
            ]
        }
    )
    monkeypatch.setattr(expert_llm_module, "get_default_llm_client", lambda: llm)
    candidate = _candidate("R1")

    asyncio.run(
        expert_llm_module.analyze_findings_with_llm(
            agent_name="agent_name",
            agent_title="agent_title",
            candidates=[candidate],
            evidence_snippets=["snippet"],
        )
    )

    assert candidate.evaluation_result == "not_triggered"
    assert candidate.confidence == 0.92


def test_analyze_findings_rejects_share_threshold_when_product_revenue_is_zero(monkeypatch):
    llm = _ScriptedLLM(
        {
            "items": [
                {
                    "rule_id": "HKIPO-086",
                    "evaluation_result": "triggered",
                    "severity": "high",
                    "confidence": 0.85,
                    "reasoning_summary": (
                        "第61页披露尚无产品销售收入，但核心产品依赖外部授权，"
                        "因此产品收入占比超过50%。"
                    ),
                    "needs_more_evidence": False,
                }
            ]
        }
    )
    monkeypatch.setattr(expert_llm_module, "get_default_llm_client", lambda: llm)
    candidate = _candidate("HKIPO-086")
    candidate.trigger_condition = "核心产品收入占比 > 50% 的产品依赖外部授权技术"

    asyncio.run(
        expert_llm_module.analyze_findings_with_llm(
            agent_name="technology_agent",
            agent_title="Technology specialist",
            candidates=[candidate],
            evidence_snippets=["第61页：目前并无自产品销售产生的收益。"],
        )
    )

    assert candidate.evaluation_result == "insufficient_evidence"
    assert candidate.confidence == 0.49
    assert "不得判定该阈值已触发" in candidate.reasoning_summary


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
