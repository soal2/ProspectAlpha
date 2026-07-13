from pathlib import Path

import riskagent.agents.supervisor as supervisor_module
from riskagent.agent_experts import AgentSpec
from riskagent.agents.orchestrator import ReplacementDecision, StrategyDecision
from riskagent.agents.supervisor import AgentTeamSupervisor
from riskagent.retrieval import ChunkSearchResult
from riskagent.risk_rules import RiskRuleRepository


def _strategy(*, selected=("financial_agent", "legal_agent"), **_kwargs):
    assignments = []
    for agent_name in selected:
        module = agent_name.removesuffix("_agent")
        assignments.append(
            {
                "module_id": module,
                "agent_name": agent_name,
                "module": f"{module} risk",
                "goal": f"Analyze {module} risk",
                "expected_outputs": ["RiskFinding"],
            }
        )
    return StrategyDecision.model_validate(
        {
            "summary": "dynamic test plan",
            "focus_areas": list(selected),
            "selected_agents": list(selected),
            "assignments": assignments,
        }
    )


def _chunk_retriever(**_kwargs):
    return [
        ChunkSearchResult(
            chunk_id="chunk_test_001",
            document_id="doc_test",
            doc_key="test",
            chunk_type="text",
            text=(
                "公司存在关联交易、赎回安排，经营活动现金流持续为负；"
                "公司前五大供应商采购占比较高，存在供应商集中风险。"
            ),
            page_idx=0,
            source_file="test.pdf",
            source_item_indices=[0],
            bbox_refs=[{"page_idx": 0, "bbox": [1, 2, 3, 4], "source_item_index": 0}],
            merged_bboxes=[{"page_idx": 0, "bbox": [1, 2, 3, 4]}],
            image_path=None,
            mineru_img_path=None,
            table_html=None,
            metadata={"section_path": "风险因素"},
            score=0.9,
        )
    ]


def test_agent_supervisor_runs_only_strategy_selected_threads(tmp_path):
    repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    supervisor = AgentTeamSupervisor(
        repository=repo,
        strategy_provider=_strategy,
        chunk_retriever=_chunk_retriever,
        runtime_root=tmp_path / "agent-runtime",
    )

    result = supervisor.run(
        run_id="run_dynamic_test",
        company_name="测试 IPO",
        user_requirement="分析招股书风险",
        document_summary="关联交易与现金流风险",
        document_id="doc_test",
        max_findings=10,
    )

    assert hasattr(supervisor.graph, "invoke")
    assert result.paused is False
    assert result.evaluations
    assert result.strategy_plan["selected_agents"] == ["financial_agent", "legal_agent"]
    assert {item["agent_name"] for item in result.team_state} == {
        "financial_agent",
        "legal_agent",
    }
    assert {item["agent_name"] for item in result.module_results} == {
        "financial_agent",
        "legal_agent",
    }
    assert all(not thread.name.startswith("a2a-expert-run_dynamic_test") for thread in __import__("threading").enumerate())
    assigned_task_ids = [
        event["payload"]["task_id"]
        for event in result.events
        if event["event_type"] == "task_assigned"
    ]
    assert len(assigned_task_ids) == len(set(assigned_task_ids)) == 2
    assert all(task_id.startswith("run_dynamic_test::") for task_id in assigned_task_ids)


def test_supervisor_executes_new_catalog_expert_without_code_chain_changes(tmp_path):
    repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    retrieval_calls = []

    def recording_retriever(**kwargs):
        retrieval_calls.append(kwargs)
        return _chunk_retriever(**kwargs)

    custom = AgentSpec(
        name="supply_chain_expert",
        title="Supply chain specialist",
        categories=("客户/供应商依赖",),
    )
    supervisor = AgentTeamSupervisor(
        repository=repo,
        agent_specs=[custom],
        strategy_provider=lambda **_kwargs: _strategy(
            selected=("supply_chain_expert",)
        ),
        chunk_retriever=recording_retriever,
        runtime_root=tmp_path / "agent-runtime",
    )

    result = supervisor.run(
        run_id="run_custom_expert",
        company_name="测试 IPO",
        user_requirement="分析供应链风险",
        document_summary="供应商集中",
        document_id="doc_test",
        max_findings=10,
    )

    assert result.paused is False
    assert result.strategy_plan["selected_agents"] == ["supply_chain_expert"]
    assert [item["agent_name"] for item in result.module_results] == [
        "supply_chain_expert"
    ]
    assert {item.source_type for item in result.evaluations} == {
        "supply_chain_expert"
    }
    assert retrieval_calls
    assert all(call["document_id"] == "doc_test" for call in retrieval_calls)
    assert all("Analyze supply_chain_expert risk" in call["query"] for call in retrieval_calls)


def test_expert_startup_failure_uses_strategic_replacement(tmp_path, monkeypatch):
    repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")

    def choose_replacement(*, failed_assignment, available_candidates, **_kwargs):
        return ReplacementDecision(
            module_id=failed_assignment.module_id,
            agent_name=available_candidates[0]["agent_name"],
            reason="replace startup failure",
        )

    supervisor = AgentTeamSupervisor(
        repository=repo,
        strategy_provider=_strategy,
        replacement_provider=choose_replacement,
        chunk_retriever=_chunk_retriever,
        runtime_root=tmp_path / "agent-runtime",
    )
    original_start = supervisor_module.TeamRuntime.start_expert
    failed = {"value": False}

    def fail_legal_start_once(runtime, agent_name, *args, **kwargs):
        if agent_name == "legal_agent" and not failed["value"]:
            failed["value"] = True
            raise RuntimeError("legal expert failed during initialization")
        return original_start(runtime, agent_name, *args, **kwargs)

    monkeypatch.setattr(
        supervisor_module.TeamRuntime,
        "start_expert",
        fail_legal_start_once,
    )
    result = supervisor.run(
        run_id="run_startup_replacement",
        company_name="测试 IPO",
        user_requirement="分析招股书风险",
        document_summary="关联交易与现金流风险",
        document_id="doc_test",
        max_findings=10,
    )

    assert result.paused is False
    assert {item["module_id"] for item in result.module_results} == {
        "financial",
        "legal",
    }
    assert any(
        item.get("module_id") == "legal" and item.get("status") == "completed"
        for item in result.replacement_history
    )


def test_invalid_expert_artifact_fails_before_task_completion_and_is_replaced(tmp_path):
    repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")

    def choose_replacement(*, failed_assignment, available_candidates, **_kwargs):
        return ReplacementDecision(
            module_id=failed_assignment.module_id,
            agent_name=available_candidates[0]["agent_name"],
            reason="replace invalid artifact",
        )

    supervisor = AgentTeamSupervisor(
        repository=repo,
        strategy_provider=lambda **kwargs: _strategy(
            selected=("legal_agent",), **kwargs
        ),
        replacement_provider=choose_replacement,
        chunk_retriever=_chunk_retriever,
        runtime_root=tmp_path / "agent-runtime",
    )
    original_handler = supervisor._expert_handler

    def invalid_legal_handler(*, spec, **kwargs):
        if spec.name == "legal_agent":
            return lambda _payload, _peer: {"not": "ExpertTaskOutput"}
        return original_handler(spec=spec, **kwargs)

    supervisor._expert_handler = invalid_legal_handler
    result = supervisor.run(
        run_id="run_invalid_artifact",
        company_name="测试 IPO",
        user_requirement="分析招股书风险",
        document_summary="关联交易风险",
        document_id="doc_test",
        max_findings=10,
    )

    assert result.paused is False
    assert [item["module_id"] for item in result.module_results] == ["legal"]
    assert any(
        item.get("module_id") == "legal" and item.get("status") == "completed"
        for item in result.replacement_history
    )


def test_invalid_strategy_interrupts_then_manual_selection_resumes(tmp_path):
    repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")

    def invalid_strategy(**_kwargs):
        raise ValueError("invalid structured strategy")

    supervisor = AgentTeamSupervisor(
        repository=repo,
        strategy_provider=invalid_strategy,
        chunk_retriever=_chunk_retriever,
        runtime_root=tmp_path / "agent-runtime",
    )
    paused = supervisor.run(
        run_id="run_manual_resume",
        company_name="测试 IPO",
        user_requirement="分析招股书风险",
        document_summary="关联交易",
        document_id="doc_test",
        max_findings=10,
    )

    assert paused.paused is True
    assert "invalid structured strategy" in (paused.interrupt_reason or "")

    completed = supervisor.resume_with_selection(
        run_id="run_manual_resume",
        agent_names=["legal_agent"],
    )

    assert completed.paused is False
    assert completed.strategy_plan["selected_agents"] == ["legal_agent"]
    assert {item.source_type for item in completed.evaluations} <= {"legal_agent"}


def test_two_failed_replacements_pause_then_targeted_manual_recovery_preserves_success(
    tmp_path,
):
    repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")

    def choose_replacement(*, failed_assignment, available_candidates, **_kwargs):
        selected = available_candidates[0]["agent_name"]
        return ReplacementDecision(
            module_id=failed_assignment.module_id,
            agent_name=selected,
            reason="test replacement",
        )

    supervisor = AgentTeamSupervisor(
        repository=repo,
        strategy_provider=_strategy,
        replacement_provider=choose_replacement,
        chunk_retriever=_chunk_retriever,
        runtime_root=tmp_path / "agent-runtime",
    )
    original_handler = supervisor._expert_handler
    failures_enabled = {"value": True}

    def handler_with_failures(*, spec, **kwargs):
        if failures_enabled["value"] and spec.name != "financial_agent":
            def fail(_payload, _peer):
                raise RuntimeError(f"{spec.name} unavailable")

            return fail
        return original_handler(spec=spec, **kwargs)

    supervisor._expert_handler = handler_with_failures
    paused = supervisor.run(
        run_id="run_targeted_recovery",
        company_name="测试 IPO",
        user_requirement="分析招股书风险",
        document_summary="关联交易与现金流风险",
        document_id="doc_test",
        max_findings=10,
    )

    assert paused.paused is True
    assert paused.strategy_plan["selected_agents"] == ["financial_agent", "legal_agent"]
    assert [item["module_id"] for item in paused.module_results] == ["financial"]
    assert len([item for item in paused.replacement_history if item.get("round")]) == 2

    failures_enabled["value"] = False
    completed = supervisor.resume_with_selection(
        run_id="run_targeted_recovery",
        agent_names=["legal_agent"],
    )

    assert completed.paused is False
    assert completed.strategy_plan["selected_agents"] == ["financial_agent", "legal_agent"]
    assert {item["module_id"] for item in completed.module_results} == {
        "financial",
        "legal",
    }
    assert {item.source_type for item in completed.evaluations}.issuperset(
        {"financial_agent", "legal_agent"}
    )
    assert {
        item["current_module"]: item["status"] for item in completed.team_state
    } == {"financial": "completed", "legal": "completed"}
    assert any(item.get("manual") is True for item in completed.replacement_history)
