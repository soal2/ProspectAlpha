from pathlib import Path
from typing import Any

import riskagent.agents.supervisor as supervisor_module
from riskagent.a2a import MessageType
from riskagent.agent_experts import AgentSpec
from riskagent.agents.orchestrator import StrategyAssignment
from riskagent.agents.supervisor import AgentTeamSupervisor
from riskagent.risk_rules import RiskRuleRepository


class _PeerContext:
    message_type = MessageType.TASK
    available_peers: list[dict[str, Any]] = []


def _build_handler(tmp_path: Path):
    repository = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    spec = AgentSpec(
        name="related_party_agent",
        title="Related party specialist",
        categories=("关联交易",),
    )
    assignment = StrategyAssignment(
        module_id="related_party",
        agent_name=spec.name,
        module="关联交易风险",
        goal="Analyze related party transactions",
        expected_outputs=["RiskFinding"],
    )
    supervisor = AgentTeamSupervisor(
        repository=repository,
        agent_specs=[spec],
        runtime_root=tmp_path / "agent-runtime",
    )
    return supervisor._expert_handler(
        spec=spec,
        assignment=assignment,
        text="公司存在关联交易及持续采购安排。",
        rules=repository.select_rules(),
        rule_snapshot=repository.load().model_dump(),
    ), assignment


def test_expert_handler_applies_llm_second_pass(tmp_path, monkeypatch):
    async def enrich_candidates(**kwargs: Any):
        for candidate in kwargs["candidates"]:
            candidate.reasoning_summary = "LLM 二判：关联交易披露显示持续采购安排，需要结合定价公允性进一步核验。"
        return []

    monkeypatch.setattr(
        supervisor_module,
        "analyze_findings_with_llm",
        enrich_candidates,
    )
    handler, assignment = _build_handler(tmp_path)

    result = handler({"assignment": assignment.model_dump()}, _PeerContext())

    evaluations = result["evaluations"]
    assert evaluations
    assert evaluations[0]["reasoning_summary"] == (
        "LLM 二判：关联交易披露显示持续采购安排，需要结合定价公允性进一步核验。"
    )
    assert not evaluations[0]["reasoning_summary"].startswith("文本命中规则关键词：")


def test_expert_handler_keeps_rule_results_when_llm_fails(tmp_path, monkeypatch):
    async def fail_llm(**_kwargs: Any):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(
        supervisor_module,
        "analyze_findings_with_llm",
        fail_llm,
    )
    handler, assignment = _build_handler(tmp_path)

    result = handler({"assignment": assignment.model_dump()}, _PeerContext())

    evaluations = result["evaluations"]
    assert len(evaluations) == 6
    assert all(item["evaluation_result"] == "triggered" for item in evaluations)
    assert evaluations[0]["reasoning_summary"].startswith("文本命中规则关键词：")
    assert any(
        "LLM 二判失败" in limitation
        for limitation in result["module_result"]["limitations"]
    )
