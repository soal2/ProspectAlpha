"""Tests for the multi-round expert debate phase.

Validates that:
1. PeerCritique / FindingRevision / ArbiterRuling contracts enforce validation.
2. arbitrate_debate() returns a strict ArbiterRuling.
3. run_debate_phase() executes 2 rounds (critique then revision) + L3 arbitration,
   emitting the expected critique/revision/ruling artifact shapes.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from riskagent.agents import debate as debate_module
from riskagent.agents import llm_resilience as llm_resilience_module
from riskagent.agents.debate import DebatePhaseResult, run_debate_phase
from riskagent.agents.orchestrator import (
    ArbiterRuling,
    FindingRevision,
    PeerCritique,
    arbitrate_debate,
)
from riskagent.providers.llm import LLMTransientError


class _ScriptedLLM:
    """Dispatches canned JSON responses by inspecting prompt content.

    Each chat_json call scans both system and user messages for a marker string
    and returns the matching payload. This avoids ordering races from
    ``asyncio.gather`` in the debate phase.

    Values may be either:
      - a dict (returned as the JSON payload), or
      - an Exception instance (raised), so partial-failure scenarios can be
        scripted without a separate test double.
    """

    def __init__(self, responses_by_prompt: dict[str, Any]):
        self.responses_by_prompt = responses_by_prompt
        self.calls: list[list[dict[str, str]]] = []

    async def chat_json(self, messages, *, temperature: float = 0.2):
        self.calls.append(messages)
        system = messages[0]["content"] if messages else ""
        user = messages[1]["content"] if len(messages) > 1 else ""
        haystack = f"{system}\n{user}"
        for marker, payload in self.responses_by_prompt.items():
            if marker in haystack:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"No scripted response matched prompt: {haystack[:200]!r}")


def _finding(agent: str, finding_id: str) -> dict[str, Any]:
    return {
        "finding_id": finding_id,
        "agent_name": agent,
        "summary": f"{agent} 风险立论 {finding_id}",
        "severity": "major",
    }


def test_peer_critique_contract_rejects_extra_fields_and_invalid_severity():
    with pytest.raises(ValueError):
        PeerCritique.model_validate(
            {
                "critic_agent": "qin_bingwen_financial_agent",
                "target_agent": "lu_guanlan_legal_agent",
                "finding_ids": ["f-1"],
                "critique_summary": "证据链不充分",
                "suggested_revisions": ["补充现金流佐证"],
                "severity": "catastrophic",
                "unexpected_field": "boom",
            }
        )


def test_finding_revision_contract_enforces_required_fields():
    with pytest.raises(ValueError):
        FindingRevision.model_validate(
            {
                "agent_name": "qin_bingwen_financial_agent",
                # missing revised_summary and revision_reason
            }
        )


def test_arbiter_ruling_contract_accepts_valid_decision():
    ruling = ArbiterRuling.model_validate(
        {
            "decision": "proceed",
            "reasoning": "证据链闭合，允许进入交叉验证。",
            "required_supplements": [],
            "flagged_finding_ids": [],
            "approved_finding_ids": ["f-1", "f-2"],
        }
    )
    assert ruling.decision == "proceed"
    assert ruling.approved_finding_ids == ["f-1", "f-2"]


def test_arbiter_ruling_contract_rejects_unknown_decision():
    with pytest.raises(ValueError):
        ArbiterRuling.model_validate(
            {
                "decision": "force_accept",
                "reasoning": "invalid branch",
            }
        )


def test_arbitrate_debate_returns_strict_arbiter_ruling(monkeypatch):
    payload = {
        "decision": "request_supplement",
        "reasoning": "立论仍需补充近三年应收账款回款明细。",
        "required_supplements": ["补充应收账款回款表"],
        "flagged_finding_ids": ["f-1"],
        "approved_finding_ids": ["f-2"],
    }
    llm = _ScriptedLLM({"质证意见": payload, "仲裁": payload, "红队": payload})
    monkeypatch.setattr(llm_resilience_module, "get_default_llm_client", lambda: llm)

    ruling = asyncio.run(
        arbitrate_debate(
            company_name="示例科技",
            findings_with_critiques=[
                {
                    "finding": _finding("qin_bingwen_financial_agent", "f-1"),
                    "critiques": [],
                    "revision": None,
                }
            ],
            arbiter_agent_name="zhou_shenyan_redteam_agent",
        )
    )
    assert isinstance(ruling, ArbiterRuling)
    assert ruling.decision == "request_supplement"
    assert ruling.required_supplements == ["补充应收账款回款表"]
    assert ruling.flagged_finding_ids == ["f-1"]
    # System prompt should mention arbiter persona
    system_text = llm.calls[0][0]["content"]
    assert "周慎言" in system_text or "redteam" in system_text.lower()


def test_run_debate_phase_executes_two_rounds_and_arbitration(monkeypatch):
    """3 experts → 3*2=6 critiques (round 1) + 3 revisions (round 2) + 1 arbiter ruling."""
    agents = [
        "qin_bingwen_financial_agent",
        "lu_guanlan_legal_agent",
        "ye_zhiyuan_market_agent",
    ]
    findings = [
        _finding(agent, f"f-{idx + 1}") for idx, agent in enumerate(agents)
    ]

    critique_payload = {
        "critic_agent": "placeholder",  # debate.py overrides with the actual critic
        "target_agent": "placeholder",
        "finding_ids": ["f-1"],
        "critique_summary": "证据链不充分，建议补充近三年数据。",
        "suggested_revisions": ["补充应收账款回款明细"],
        "severity": "minor",
    }
    revision_payload = {
        "agent_name": "placeholder",  # debate.py overrides
        "original_finding_ids": ["f-1"],
        "revised_summary": "已补充现金流佐证，调整立论。",
        "added_evidence_ids": ["e-new"],
        "removed_finding_ids": [],
        "revision_reason": "吸收同侪质证意见。",
    }
    arbiter_payload = {
        "decision": "proceed",
        "reasoning": "三轮立论已覆盖核心风险且证据闭合。",
        "required_supplements": [],
        "flagged_finding_ids": [],
        "approved_finding_ids": ["f-1", "f-2", "f-3"],
    }

    llm = _ScriptedLLM(
        {
            "对同侪 finding 进行质证": critique_payload,
            "对自身 finding 进行修订": revision_payload,
            "周慎言": arbiter_payload,
        }
    )
    monkeypatch.setattr(llm_resilience_module, "get_default_llm_client", lambda: llm)

    result = asyncio.run(
        run_debate_phase(
            company_name="示例科技",
            findings=findings,
            module_results=[],
            arbiter_agent_name="zhou_shenyan_redteam_agent",
        )
    )

    assert isinstance(result, DebatePhaseResult)
    # Round 1 critiques: N*(N-1) = 6
    assert len(result.critiques) == 6
    # Round 2 revisions: N = 3
    assert len(result.revisions) == 3
    # Arbiter ruling
    assert result.ruling.decision == "proceed"
    assert result.ruling.approved_finding_ids == ["f-1", "f-2", "f-3"]
    # decision shortcut surfaces the same value
    assert result.decision == "proceed"

    # History structure
    history = result.history
    assert history["arbiter_agent_name"] == "zhou_shenyan_redteam_agent"
    assert len(history["rounds"]) == 2
    assert history["rounds"][0]["kind"] == "critique"
    assert history["rounds"][1]["kind"] == "revision"
    # Round 1 should have 6 items, round 2 should have 3
    assert len(history["rounds"][0]["items"]) == 6
    assert len(history["rounds"][1]["items"]) == 3

    # All LLM calls were exercised (6 + 3 + 1 = 10)
    assert len(llm.calls) == 10


def test_run_debate_phase_sync_wraps_async(monkeypatch):
    """The sync wrapper should produce identical types as the async entrypoint."""
    revision_payload = {
        "agent_name": "qin_bingwen_financial_agent",
        "original_finding_ids": ["f-1"],
        "revised_summary": "修订后的立论摘要。",
        "added_evidence_ids": ["e-2"],
        "removed_finding_ids": [],
        "revision_reason": "重新组织证据链。",
    }
    arbiter_payload = {
        "decision": "escalate_human",
        "reasoning": "证据严重冲突，需人工介入。",
        "required_supplements": [],
        "flagged_finding_ids": ["f-1"],
        "approved_finding_ids": [],
    }
    llm = _ScriptedLLM(
        {
            "对同侪 finding 进行质证": revision_payload,  # not used: only 1 agent → 0 critiques
            "对自身 finding 进行修订": revision_payload,
            "周慎言": arbiter_payload,
        }
    )
    monkeypatch.setattr(llm_resilience_module, "get_default_llm_client", lambda: llm)

    result = debate_module.run_debate_phase_sync(
        company_name="示例科技",
        findings=[_finding("qin_bingwen_financial_agent", "f-1")],
    )
    assert isinstance(result, DebatePhaseResult)
    assert result.decision == "escalate_human"


def _disable_retry_backoff(monkeypatch):
    """Speed up debate tests that exercise transient-failure paths."""
    monkeypatch.setattr(llm_resilience_module, "LLM_RETRY_INITIAL_SECONDS", 0.0)
    monkeypatch.setattr(llm_resilience_module, "LLM_RETRY_MAX_SECONDS", 0.0)


def test_debate_phase_continues_with_partial_critique_failures(monkeypatch):
    """6 critiques scheduled; 1 fails with LLMTransientError after retries exhausted.

    Debate should still return with 5 critiques, 3 revisions, and a ruling,
    while recording the partial failure in history.
    """
    _disable_retry_backoff(monkeypatch)

    agents = [
        "qin_bingwen_financial_agent",
        "lu_guanlan_legal_agent",
        "ye_zhiyuan_market_agent",
    ]
    findings = [_finding(agent, f"f-{idx + 1}") for idx, agent in enumerate(agents)]

    critique_payload = {
        "critic_agent": "placeholder",
        "target_agent": "placeholder",
        "finding_ids": ["f-1"],
        "critique_summary": "证据链不充分。",
        "suggested_revisions": ["补充数据。"],
        "severity": "minor",
    }
    revision_payload = {
        "agent_name": "placeholder",
        "original_finding_ids": ["f-1"],
        "revised_summary": "修订后的立论。",
        "added_evidence_ids": [],
        "removed_finding_ids": [],
        "revision_reason": "吸收质证。",
    }
    arbiter_payload = {
        "decision": "proceed",
        "reasoning": "证据闭合。",
        "required_supplements": [],
        "flagged_finding_ids": [],
        "approved_finding_ids": ["f-1", "f-2", "f-3"],
    }

    # The user payload JSON serializes both agent names; this marker targets
    # exactly one of the 6 critique pairs.
    failing_pair_marker = (
        '"critic_agent": "lu_guanlan_legal_agent", '
        '"target_agent": "ye_zhiyuan_market_agent"'
    )

    llm = _ScriptedLLM(
        {
            failing_pair_marker: LLMTransientError("simulated transient"),  # 1 critique fails
            "对同侪 finding 进行质证": critique_payload,  # the other 5 critiques succeed
            "对自身 finding 进行修订": revision_payload,
            "周慎言": arbiter_payload,
        }
    )
    monkeypatch.setattr(llm_resilience_module, "get_default_llm_client", lambda: llm)

    result = asyncio.run(
        run_debate_phase(
            company_name="示例科技",
            findings=findings,
            arbiter_agent_name="zhou_shenyan_redteam_agent",
        )
    )

    assert isinstance(result, DebatePhaseResult)
    assert len(result.critiques) == 5  # 6 scheduled - 1 failed
    assert len(result.revisions) == 3
    assert result.decision == "proceed"
    assert result.partial_failure_count == 1

    failures = result.history["failures"]
    assert len(failures["critique"]) == 1
    assert failures["critique"][0]["actor"] == "lu_guanlan_legal_agent"
    assert failures["critique"][0]["target"] == "ye_zhiyuan_market_agent"
    assert failures["critique"][0]["error_class"] == "LLMTransientError"
    # No revision failures in this scenario
    assert failures["revision"] == []


def test_debate_phase_raises_DebateLLMError_when_all_critiques_fail(monkeypatch):
    """When every critique fails, run_debate_phase raises DebateLLMError(phase='critique')."""
    _disable_retry_backoff(monkeypatch)

    from riskagent.agents.exceptions import DebateLLMError

    agents = [
        "qin_bingwen_financial_agent",
        "lu_guanlan_legal_agent",
        "ye_zhiyuan_market_agent",
    ]
    findings = [_finding(agent, f"f-{idx + 1}") for idx, agent in enumerate(agents)]

    llm = _ScriptedLLM(
        {
            "对同侪 finding 进行质证": LLMTransientError("simulated transient"),
            "对自身 finding 进行修订": {
                "agent_name": "placeholder",
                "original_finding_ids": ["f-1"],
                "revised_summary": "x",
                "added_evidence_ids": [],
                "removed_finding_ids": [],
                "revision_reason": "x",
            },
            "周慎言": {
                "decision": "proceed",
                "reasoning": "x",
                "required_supplements": [],
                "flagged_finding_ids": [],
                "approved_finding_ids": [],
            },
        }
    )
    monkeypatch.setattr(llm_resilience_module, "get_default_llm_client", lambda: llm)

    with pytest.raises(DebateLLMError) as exc_info:
        asyncio.run(
            run_debate_phase(
                company_name="示例科技",
                findings=findings,
                arbiter_agent_name="zhou_shenyan_redteam_agent",
            )
        )
    err = exc_info.value
    assert err.phase == "critique"
    assert err.failed == err.total == 6  # N*(N-1) = 3*2 = 6 critiques all fail


def test_run_debate_phase_no_focused_round_when_arbiter_proceeds(monkeypatch):
    """When arbiter rules 'proceed', no Round 3 focused debate is triggered."""
    llm_resilience_module.set_default_breaker(llm_resilience_module.CircuitBreaker())
    agents = [
        "qin_bingwen_financial_agent",
        "lu_guanlan_legal_agent",
    ]
    findings = [_finding(agent, f"f-{idx + 1}") for idx, agent in enumerate(agents)]

    critique_payload = {
        "critic_agent": "placeholder",
        "target_agent": "placeholder",
        "finding_ids": ["f-1"],
        "critique_summary": "证据不充分。",
        "suggested_revisions": ["补充数据。"],
        "severity": "minor",
    }
    revision_payload = {
        "agent_name": "placeholder",
        "original_finding_ids": ["f-1"],
        "revised_summary": "已修订。",
        "added_evidence_ids": [],
        "removed_finding_ids": [],
        "revision_reason": "吸收质证。",
    }
    arbiter_payload = {
        "decision": "proceed",
        "reasoning": "证据闭合。",
        "required_supplements": [],
        "flagged_finding_ids": [],
        "approved_finding_ids": ["f-1", "f-2"],
    }

    llm = _ScriptedLLM(
        {
            "对同侪 finding 进行质证": critique_payload,
            "对自身 finding 进行修订": revision_payload,
            "周慎言": arbiter_payload,
        }
    )
    monkeypatch.setattr(llm_resilience_module, "get_default_llm_client", lambda: llm)

    result = asyncio.run(
        run_debate_phase(
            company_name="示例科技",
            findings=findings,
            arbiter_agent_name="zhou_shenyan_redteam_agent",
        )
    )

    assert result.history["focused_debate_triggered"] is False
    # Only 2 rounds in history (critique + revision), no focused debate rounds
    assert len(result.history["rounds"]) == 2
    round_kinds = [r["kind"] for r in result.history["rounds"]]
    assert "focused_critique" not in round_kinds
    assert "focused_response" not in round_kinds


def test_run_debate_phase_triggers_focused_round_on_request_supplement(monkeypatch):
    """When arbiter rules 'request_supplement' with flagged_finding_ids,
    Round 3 focused debate is triggered for contested findings only.
    """
    llm_resilience_module.set_default_breaker(llm_resilience_module.CircuitBreaker())
    agents = [
        "qin_bingwen_financial_agent",
        "lu_guanlan_legal_agent",
        "ye_zhiyuan_market_agent",
    ]
    findings = [_finding(agent, f"f-{idx + 1}") for idx, agent in enumerate(agents)]

    critique_payload = {
        "critic_agent": "placeholder",
        "target_agent": "placeholder",
        "finding_ids": ["f-1"],
        "critique_summary": "证据不充分。",
        "suggested_revisions": ["补充数据。"],
        "severity": "major",
    }
    revision_payload = {
        "agent_name": "placeholder",
        "original_finding_ids": ["f-1"],
        "revised_summary": "已修订。",
        "added_evidence_ids": [],
        "removed_finding_ids": [],
        "revision_reason": "吸收质证。",
    }
    # Arbiter flags f-1 (belongs to qin_bingwen_financial_agent) for supplement
    arbiter_payload = {
        "decision": "request_supplement",
        "reasoning": "f-1 证据不充分，需补充近三年应收账款明细。",
        "required_supplements": ["补充应收账款回款表"],
        "flagged_finding_ids": ["f-1"],
        "approved_finding_ids": ["f-2", "f-3"],
    }
    # Round 3 focused critique/response payloads
    focused_critique_payload = {
        "critic_agent": "placeholder",
        "target_agent": "placeholder",
        "finding_ids": ["f-1"],
        "critique_summary": "争议焦点：应收账款回款证据不足。",
        "suggested_revisions": ["补充银行流水佐证"],
        "severity": "major",
    }
    focused_response_payload = {
        "agent_name": "placeholder",
        "original_finding_ids": ["f-1"],
        "revised_summary": "已补充银行流水佐证。",
        "added_evidence_ids": ["e-bank"],
        "removed_finding_ids": [],
        "revision_reason": "回应聚焦质证。",
    }

    llm = _ScriptedLLM(
        {
            "对同侪 finding 进行质证": critique_payload,
            "对自身 finding 进行修订": revision_payload,
            "第三轮聚焦辩论": focused_response_payload,
            "聚焦质证": focused_critique_payload,
            "周慎言": arbiter_payload,
        }
    )
    monkeypatch.setattr(llm_resilience_module, "get_default_llm_client", lambda: llm)

    result = asyncio.run(
        run_debate_phase(
            company_name="示例科技",
            findings=findings,
            arbiter_agent_name="zhou_shenyan_redteam_agent",
        )
    )

    assert result.history["focused_debate_triggered"] is True
    # 4 round entries: critique, revision, focused_critique, focused_response
    assert len(result.history["rounds"]) == 4
    assert result.history["rounds"][0]["kind"] == "critique"
    assert result.history["rounds"][1]["kind"] == "revision"
    assert result.history["rounds"][2]["kind"] == "focused_critique"
    assert result.history["rounds"][2]["round_type"] == "focused_debate"
    assert result.history["rounds"][2]["contested_finding_ids"] == ["f-1"]
    assert result.history["rounds"][3]["kind"] == "focused_response"
    assert result.history["rounds"][3]["round_type"] == "focused_debate"

    # f-1 belongs to qin_bingwen_financial_agent; the other 2 agents provide focused critiques
    focused_critiques = result.history["rounds"][2]["items"]
    assert len(focused_critiques) == 2  # 2 non-flagged agents critique f-1
    critic_agents = {c["critic_agent"] for c in focused_critiques}
    assert "qin_bingwen_financial_agent" not in critic_agents

    # 1 focused response from the flagged agent
    focused_responses = result.history["rounds"][3]["items"]
    assert len(focused_responses) == 1

    # The ruling is still surfaced on the result
    assert result.ruling.decision == "request_supplement"
    assert result.decision == "request_supplement"


def test_focused_round_partial_failure_tolerated(monkeypatch):
    """Round 3 partial failures are tolerated and recorded in failure_log."""
    _disable_retry_backoff(monkeypatch)
    llm_resilience_module.set_default_breaker(llm_resilience_module.CircuitBreaker())

    agents = [
        "qin_bingwen_financial_agent",
        "lu_guanlan_legal_agent",
        "ye_zhiyuan_market_agent",
    ]
    findings = [_finding(agent, f"f-{idx + 1}") for idx, agent in enumerate(agents)]

    critique_payload = {
        "critic_agent": "placeholder",
        "target_agent": "placeholder",
        "finding_ids": ["f-1"],
        "critique_summary": "证据不充分。",
        "suggested_revisions": ["补充数据。"],
        "severity": "major",
    }
    revision_payload = {
        "agent_name": "placeholder",
        "original_finding_ids": ["f-1"],
        "revised_summary": "已修订。",
        "added_evidence_ids": [],
        "removed_finding_ids": [],
        "revision_reason": "吸收质证。",
    }
    arbiter_payload = {
        "decision": "request_supplement",
        "reasoning": "f-1 证据不充分。",
        "required_supplements": ["补充数据"],
        "flagged_finding_ids": ["f-1"],
        "approved_finding_ids": ["f-2", "f-3"],
    }
    focused_critique_payload = {
        "critic_agent": "placeholder",
        "target_agent": "placeholder",
        "finding_ids": ["f-1"],
        "critique_summary": "争议焦点。",
        "suggested_revisions": ["补充佐证"],
        "severity": "major",
    }
    focused_response_payload = {
        "agent_name": "placeholder",
        "original_finding_ids": ["f-1"],
        "revised_summary": "已补充。",
        "added_evidence_ids": [],
        "removed_finding_ids": [],
        "revision_reason": "回应。",
    }

    # Make one of the focused critiques fail (lu_guanlan targeting qin_bingwen)
    failing_focused_marker = '"critic_agent": "lu_guanlan_legal_agent"'

    llm = _ScriptedLLM(
        {
            "对同侪 finding 进行质证": critique_payload,
            "对自身 finding 进行修订": revision_payload,
            "第三轮聚焦辩论": focused_response_payload,
            failing_focused_marker: LLMTransientError("simulated transient"),
            "聚焦质证": focused_critique_payload,
            "周慎言": arbiter_payload,
        }
    )
    monkeypatch.setattr(llm_resilience_module, "get_default_llm_client", lambda: llm)

    result = asyncio.run(
        run_debate_phase(
            company_name="示例科技",
            findings=findings,
            arbiter_agent_name="zhou_shenyan_redteam_agent",
        )
    )

    # Round 3 still ran despite partial failure
    assert result.history["focused_debate_triggered"] is True
    assert len(result.history["rounds"]) == 4
    # 2 focused critiques scheduled, 1 failed → 1 success
    focused_critiques = result.history["rounds"][2]["items"]
    assert len(focused_critiques) == 1
    # Failure recorded
    assert len(result.history["failures"].get("focused_debate_critique", [])) == 1
    assert result.partial_failure_count >= 1
