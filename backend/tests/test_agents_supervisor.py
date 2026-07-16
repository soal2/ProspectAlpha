from pathlib import Path

import riskagent.agents.supervisor as supervisor_module
from riskagent.agent_experts import AgentSpec
from riskagent.agent_experts import AgentSpecRepository
from riskagent.agents.orchestrator import (
    ReplacementDecision,
    StrategyDecision,
    manual_strategy_decision,
)
from riskagent.agents.supervisor import AgentTeamSupervisor
from riskagent.database import Base, make_engine
from riskagent.risk_rules import RiskRuleRepository
from riskagent.skills.risk_extract import RuleEvaluation
from sqlalchemy.orm import sessionmaker


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


def _legacy_agent_specs() -> list[AgentSpec]:
    """4 legacy generic specs so alias resolution does not rewrite agent names.

    Tests that exercise the legacy ``financial_agent``/``legal_agent`` aliases
    pass this roster explicitly to avoid coupling to whatever the dev DB
    currently holds (which now contains 12 persona specs after Task 1).
    """

    return [
        AgentSpec(name="financial_agent", title="Financial specialist"),
        AgentSpec(name="legal_agent", title="Legal and compliance specialist"),
        AgentSpec(name="market_agent", title="Market and industry specialist"),
        AgentSpec(name="business_agent", title="Business and disclosure specialist"),
    ]


def _evaluation(*, result: str, confidence: float, source: str) -> RuleEvaluation:
    return RuleEvaluation(
        rule_set_id="rules",
        rule_version="v1",
        rule_id="R1",
        dimension_code="D1",
        rule_type="qualitative",
        category="测试",
        title="测试规则",
        trigger_condition="测试条件",
        evaluation_result=result,
        reasoning_summary="测试结论",
        matched_terms=["测试"],
        evidence_snippets=["第 1 页测试证据"],
        source_type=source,
        confidence=confidence,
    )


def test_dedupe_evaluations_keeps_stronger_later_conclusion():
    deduped = supervisor_module._dedupe_evaluations(
        [
            _evaluation(result="insufficient_evidence", confidence=0.4, source="first"),
            _evaluation(result="triggered", confidence=0.8, source="replacement"),
        ]
    )

    assert len(deduped) == 1
    assert deduped[0].evaluation_result == "triggered"
    assert deduped[0].source_type == "replacement"


def test_dedupe_evaluations_does_not_bias_conflicts_toward_risk():
    deduped = supervisor_module._dedupe_evaluations(
        [
            _evaluation(result="triggered", confidence=0.6, source="first"),
            _evaluation(result="not_triggered", confidence=0.9, source="reviewer"),
        ]
    )

    assert deduped[0].evaluation_result == "not_triggered"
    assert deduped[0].source_type == "reviewer"


def test_agent_supervisor_runs_only_strategy_selected_threads(tmp_path):
    repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    supervisor = AgentTeamSupervisor(
        repository=repo,
        agent_specs=_legacy_agent_specs(),
        strategy_provider=_strategy,
        runtime_root=tmp_path / "agent-runtime",
    )

    result = supervisor.run(
        run_id="run_dynamic_test",
        company_name="测试 IPO",
        user_requirement="分析招股书风险",
        document_summary="关联交易与现金流风险",
        text="公司存在关联交易、赎回安排，经营活动现金流持续为负。",
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


def test_manual_strategy_decision_accepts_persona_agent_names(tmp_path):
    repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    engine = make_engine(f"sqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    supervisor = AgentTeamSupervisor(
        repository=repo,
        agent_spec_repository=AgentSpecRepository(session_factory=session_factory),
        runtime_root=tmp_path / "agent-runtime",
    )

    result = manual_strategy_decision(
        agent_names=["qin_bingwen_financial_agent", "wen_shouzheng_legal_agent"],
        available_agents=supervisor.agent_options(),
    )

    assert result.selected_agents == [
        "qin_bingwen_financial_agent",
        "wen_shouzheng_legal_agent",
    ]
    assert [assignment.agent_name for assignment in result.assignments] == [
        "qin_bingwen_financial_agent",
        "wen_shouzheng_legal_agent",
    ]
    assert result.assignments[0].module_id == "module_qin_bingwen_financial"
    assert result.assignments[1].module_id == "module_wen_shouzheng_legal"


def test_supervisor_executes_new_catalog_expert_without_code_chain_changes(tmp_path):
    repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    custom = AgentSpec(
        name="supply_chain_expert",
        title="Supply chain specialist",
        categories=("供应商集中",),
    )
    supervisor = AgentTeamSupervisor(
        repository=repo,
        agent_specs=[custom],
        strategy_provider=lambda **_kwargs: _strategy(
            selected=("supply_chain_expert",)
        ),
        runtime_root=tmp_path / "agent-runtime",
    )

    result = supervisor.run(
        run_id="run_custom_expert",
        company_name="测试 IPO",
        user_requirement="分析供应链风险",
        document_summary="供应商集中",
        text="公司前五大供应商采购占比较高，存在供应商集中风险。",
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
        agent_specs=_legacy_agent_specs(),
        strategy_provider=_strategy,
        replacement_provider=choose_replacement,
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
        text="公司存在关联交易、赎回安排，经营活动现金流持续为负。",
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
        agent_specs=_legacy_agent_specs(),
        strategy_provider=lambda **kwargs: _strategy(
            selected=("legal_agent",), **kwargs
        ),
        replacement_provider=choose_replacement,
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
        text="公司存在关联交易和赎回安排。",
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
        agent_specs=_legacy_agent_specs(),
        strategy_provider=invalid_strategy,
        runtime_root=tmp_path / "agent-runtime",
    )
    paused = supervisor.run(
        run_id="run_manual_resume",
        company_name="测试 IPO",
        user_requirement="分析招股书风险",
        document_summary="关联交易",
        text="公司存在关联交易和赎回安排。",
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
        agent_specs=_legacy_agent_specs(),
        strategy_provider=_strategy,
        replacement_provider=choose_replacement,
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
        text="公司存在关联交易、赎回安排，经营活动现金流持续为负。",
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


def test_each_expert_receives_its_own_system_prompt(tmp_path, monkeypatch):
    captured_prompts: dict[str, str] = {}

    async def capture_llm(**kwargs):
        captured_prompts[kwargs["agent_name"]] = kwargs["system_prompt"]
        return []

    monkeypatch.setattr(supervisor_module, "analyze_findings_with_llm", capture_llm)
    repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    supervisor = AgentTeamSupervisor(
        repository=repo,
        agent_specs=_legacy_agent_specs(),
        strategy_provider=_strategy,
        runtime_root=tmp_path / "agent-runtime",
    )

    result = supervisor.run(
        run_id="run_prompt_isolation",
        company_name="测试 IPO",
        user_requirement="分析招股书风险",
        document_summary="关联交易与现金流风险",
        text="公司存在关联交易、赎回安排，经营活动现金流持续为负。",
        max_findings=10,
    )

    assert result.paused is False
    assert set(captured_prompts) == {"financial_agent", "legal_agent"}
    financial_prompt = captured_prompts["financial_agent"]
    legal_prompt = captured_prompts["legal_agent"]
    assert financial_prompt != legal_prompt
    assert "Financial specialist" in financial_prompt
    assert "Legal and compliance specialist" in legal_prompt
    # The shared output contract survives persona differences.
    assert "输出契约" in financial_prompt and "输出契约" in legal_prompt


def test_mission_brief_broadcast_reaches_every_expert(tmp_path, monkeypatch):
    async def no_llm(**_kwargs):
        return []

    monkeypatch.setattr(supervisor_module, "analyze_findings_with_llm", no_llm)
    repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    supervisor = AgentTeamSupervisor(
        repository=repo,
        agent_specs=_legacy_agent_specs(),
        strategy_provider=_strategy,
        runtime_root=tmp_path / "agent-runtime",
    )

    result = supervisor.run(
        run_id="run_mission_brief",
        company_name="测试 IPO",
        user_requirement="分析招股书风险",
        document_summary="关联交易与现金流风险",
        text="公司存在关联交易、赎回安排，经营活动现金流持续为负。",
        max_findings=10,
    )

    assert result.paused is False
    events_by_type: dict[str, list[dict]] = {}
    for event in result.events:
        events_by_type.setdefault(event["event_type"], []).append(event)
    broadcast_sent = events_by_type.get("strategy_broadcast_sent")
    assert broadcast_sent, "mission brief broadcast event missing"
    assert set(broadcast_sent[0]["payload"]["recipients"]) == {
        "financial_agent",
        "legal_agent",
    }
    responded = {
        event["payload"]["agent_name"]
        for event in events_by_type.get("strategy_broadcast_response", [])
    }
    assert responded == {"financial_agent", "legal_agent"}


def test_needs_more_evidence_requests_strategy_guidance(tmp_path, monkeypatch):
    async def llm_flags_missing_evidence(**kwargs):
        return [
            {
                "rule_id": candidate.rule_id,
                "severity": "medium",
                "confidence": 0.4,
                "reasoning_summary": "证据不足，需补充。",
                "needs_more_evidence": True,
            }
            for candidate in kwargs["candidates"]
        ]

    monkeypatch.setattr(
        supervisor_module, "analyze_findings_with_llm", llm_flags_missing_evidence
    )
    repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    supervisor = AgentTeamSupervisor(
        repository=repo,
        agent_specs=_legacy_agent_specs(),
        strategy_provider=_strategy,
        runtime_root=tmp_path / "agent-runtime",
    )

    result = supervisor.run(
        run_id="run_strategy_guidance",
        company_name="测试 IPO",
        user_requirement="分析招股书风险",
        document_summary="关联交易与现金流风险",
        text="公司存在关联交易、赎回安排，经营活动现金流持续为负。",
        max_findings=10,
    )

    assert result.paused is False
    guidance_by_agent = {
        item["agent_name"]: item.get("strategy_guidance")
        for item in result.module_results
    }
    for agent_name, guidance in guidance_by_agent.items():
        assert isinstance(guidance, dict), f"{agent_name} missing strategy guidance"
        assert guidance.get("kind") == "needs_more_evidence"
        assert guidance.get("responder") == "strategic_agent"
        assert "保留已命中的规则判断" in guidance.get("guidance", "")
        assert guidance.get("attempted_query_count", 0) >= 0
        assert isinstance(guidance.get("suggested_queries"), list)
    guidance_requests = [
        event
        for event in result.events
        if event["event_type"] == "peer_request_sent"
        and event["payload"].get("recipient") == "strategic_agent"
    ]
    assert len(guidance_requests) >= 2
