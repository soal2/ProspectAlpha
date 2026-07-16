from pathlib import Path
from typing import Any

import riskagent.agents.supervisor as supervisor_module
from riskagent.a2a import MessageType
from riskagent.agent_experts import AgentSpec
from riskagent.agents.orchestrator import StrategyAssignment
from riskagent.agents.supervisor import AgentTeamSupervisor
from riskagent.retrieval import ChunkSearchResult
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


def test_expert_handler_fails_closed_when_llm_fails(tmp_path, monkeypatch):
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
    assert all(item["evaluation_result"] != "triggered" for item in evaluations)
    assert result["module_result"]["rule_ids"] == []
    assert evaluations[0]["reasoning_summary"].startswith("规则预筛命中特定业务锚点：")
    assert any(
        "LLM 二判失败" in limitation
        for limitation in result["module_result"]["limitations"]
    )


def test_expert_owns_query_rewrite_and_emits_candidate_chunks(tmp_path, monkeypatch):
    queries_seen: list[str] = []
    llm_payloads: list[dict[str, Any]] = []

    async def rewrite_queries(**_kwargs: Any) -> list[str]:
        return ["概無重大訴訟 法律程序"]

    async def no_findings(**kwargs: Any) -> list[dict[str, Any]]:
        llm_payloads.append(kwargs)
        return []

    def retrieve(*, query: str, document_id: str, **_kwargs: Any):
        queries_seen.append(query)
        return [
            ChunkSearchResult(
                chunk_id="chunk_333",
                document_id=document_id,
                doc_key="ysyy",
                chunk_type="text",
                text="於往績記錄期間，本公司並無面臨任何重大法律訴訟。",
                page_idx=333,
                source_file="ysyy.pdf",
                source_item_indices=[1],
                bbox_refs=[],
                merged_bboxes=[{"page_idx": 333, "bbox": [10, 20, 30, 40]}],
                image_path=None,
                mineru_img_path=None,
                table_html=None,
                metadata={"section_path": "合規和法律程序"},
                score=0.82,
            )
        ]

    monkeypatch.setattr(
        supervisor_module,
        "rewrite_retrieval_queries_with_llm",
        rewrite_queries,
    )
    monkeypatch.setattr(supervisor_module, "analyze_findings_with_llm", no_findings)
    repository = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    spec = AgentSpec(
        name="legal_agent",
        title="Legal specialist",
        categories=("诉讼与纠纷",),
    )
    assignment = StrategyAssignment(
        module_id="legal",
        agent_name=spec.name,
        module="法务风险",
        goal="核查重大诉讼",
        expected_outputs=["RiskFinding: 重大诉讼风险"],
    )
    supervisor = AgentTeamSupervisor(
        repository=repository,
        agent_specs=[spec],
        chunk_retriever=retrieve,
        runtime_root=tmp_path / "agent-runtime",
    )
    handler = supervisor._expert_handler(
        run_id="run_query_rewrite",
        spec=spec,
        assignment=assignment,
        text="",
        document_id="doc_ysyy",
        user_requirement="检查是否存在重大诉讼",
        rules=repository.select_rules(),
        rule_snapshot=repository.load().model_dump(),
    )

    handler({"assignment": assignment.model_dump()}, _PeerContext())

    assert queries_seen[0] == "概無重大訴訟 法律程序"
    completed = [
        event
        for event in supervisor._take_runtime_events("run_query_rewrite")
        if event["event_type"] == "retrieval_query_completed"
    ]
    assert completed[0]["payload"]["chunks"][0]["page_number"] == 333
    assert completed[0]["payload"]["chunks"][0]["bbox"] == [10, 20, 30, 40]
    assert any(
        "第333页" in snippet
        for payload in llm_payloads
        for snippets in payload.get("evidence_by_rule", {}).values()
        for snippet in snippets
    )
