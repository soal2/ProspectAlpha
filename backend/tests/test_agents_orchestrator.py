import asyncio

import pytest

from riskagent.agents import orchestrator


ROSTER = [
    {
        "agent_name": "financial_agent",
        "title": "Financial specialist",
        "role": "财务风险专家",
        "description": "分析现金流与盈利质量",
        "capabilities": ["financial_risk_skill", "现金流"],
        "output_schema": "RiskFinding + RunEvidence",
    },
    {
        "agent_name": "legal_agent",
        "title": "Legal specialist",
        "role": "法务合规专家",
        "description": "分析诉讼与特殊权利",
        "capabilities": ["legal_compliance_skill", "诉讼"],
        "output_schema": "RiskFinding + RunEvidence",
    },
]


class _FakeLLM:
    def __init__(self, response):
        self.response = response
        self.messages = None

    async def chat_json(self, messages, *, temperature=0.2):
        self.messages = messages
        return self.response


def _plan(agent_name="financial_agent"):
    return {
        "summary": "聚焦关键风险",
        "focus_areas": ["现金流"],
        "selected_agents": [agent_name],
        "assignments": [
            {
                "module_id": "financial",
                "agent_name": agent_name,
                "module": "财务风险",
                "goal": "分析现金流风险",
                "expected_outputs": ["RiskFinding"],
            }
        ],
    }


def test_strategy_prompt_injects_live_roster_and_validates_selection(monkeypatch):
    llm = _FakeLLM(_plan())
    monkeypatch.setattr(orchestrator, "get_default_llm_client", lambda: llm)

    result = asyncio.run(
        orchestrator.orchestrate(
            user_requirement="分析 IPO 风险",
            company_name="测试公司",
            document_summary="招股书摘要",
            available_agents=ROSTER,
        )
    )

    assert result.selected_agents == ["financial_agent"]
    assert "AVAILABLE_EXPERTS_JSON" in llm.messages[0]["content"]
    assert "legal_agent" in llm.messages[0]["content"]


def test_strategy_rejects_unknown_or_internally_inconsistent_agents():
    with pytest.raises(ValueError, match="unavailable"):
        orchestrator.validate_strategy_decision(_plan("unknown_agent"), ROSTER)

    broken = _plan()
    broken["selected_agents"] = ["financial_agent", "legal_agent"]
    with pytest.raises(ValueError, match="cover every selected"):
        orchestrator.validate_strategy_decision(broken, ROSTER)

    extra = _plan()
    extra["fixed_chain"] = ["financial_agent"]
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        orchestrator.validate_strategy_decision(extra, ROSTER)


def test_manual_strategy_uses_agent_card_capabilities():
    result = orchestrator.manual_strategy_decision(
        agent_names=["legal_agent"],
        available_agents=ROSTER,
    )

    assert result.selected_agents == ["legal_agent"]
    assert result.assignments[0].agent_name == "legal_agent"
    assert "诉讼" in result.assignments[0].goal


def test_manual_strategy_rejects_duplicate_or_blank_experts():
    with pytest.raises(ValueError, match="unique"):
        orchestrator.manual_strategy_decision(
            agent_names=["legal_agent", "legal_agent"],
            available_agents=ROSTER,
        )
    with pytest.raises(ValueError, match="non-empty"):
        orchestrator.manual_strategy_decision(
            agent_names=["legal_agent", ""],
            available_agents=ROSTER,
        )


def test_integrated_report_rejects_unknown_trace_references():
    report = orchestrator.IntegratedRiskReport.model_validate(
        {
            "title": "IPO 风险报告",
            "executive_summary": "存在风险",
            "overall_risk_level": "中",
            "sections": [
                {
                    "module_id": "financial",
                    "title": "财务风险",
                    "analysis": "现金流承压",
                    "finding_ids": ["finding_unknown"],
                    "evidence_ids": ["evidence_1"],
                }
            ],
            "risk_themes": [
                {
                    "title": "现金流风险",
                    "conclusion": "现金流承压。",
                    "analysis": "现金消耗会削弱经营缓冲。",
                    "investment_impact": "盈利可见性下降。",
                    "finding_ids": ["finding_unknown"],
                    "evidence_ids": ["evidence_1"],
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="unknown references"):
        orchestrator.validate_report_references(
            report,
            module_ids={"financial"},
            finding_ids={"finding_1"},
            finding_ids_by_module={"financial": {"finding_1"}},
            evidence_by_finding={"finding_1": {"evidence_1"}},
            verification_status_by_finding={"finding_1": "supported"},
        )


def test_integrated_report_does_not_require_fixed_module_coverage():
    report = orchestrator.IntegratedRiskReport.model_validate(
        {
            "title": "IPO 风险报告",
            "executive_summary": "存在风险",
            "overall_risk_level": "中",
            "sections": [
                {
                    "module_id": "financial",
                    "title": "财务风险",
                    "analysis": "现金流承压",
                },
                {
                    "module_id": "financial",
                    "title": "重复财务风险",
                    "analysis": "重复章节",
                },
            ],
            "risk_themes": [],
        }
    )

    orchestrator.validate_report_references(
        report,
        module_ids={"financial", "legal"},
        finding_ids=set(),
        finding_ids_by_module={"financial": set(), "legal": set()},
        evidence_by_finding={},
        verification_status_by_finding={},
    )


def test_integrated_report_rejects_evidence_from_another_finding():
    report = orchestrator.IntegratedRiskReport.model_validate(
        {
            "title": "IPO 风险报告",
            "executive_summary": "存在风险",
            "overall_risk_level": "中",
            "sections": [
                {
                    "module_id": "financial",
                    "title": "财务风险",
                    "analysis": "现金流承压",
                    "finding_ids": ["finding_1"],
                    "evidence_ids": ["evidence_2"],
                }
            ],
            "risk_themes": [
                {
                    "title": "现金流风险",
                    "conclusion": "现金流承压。",
                    "analysis": "现金消耗会削弱经营缓冲。",
                    "investment_impact": "盈利可见性下降。",
                    "finding_ids": ["finding_1"],
                    "evidence_ids": ["evidence_2"],
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="evidence must belong"):
        orchestrator.validate_report_references(
            report,
            module_ids={"financial"},
            finding_ids={"finding_1"},
            finding_ids_by_module={"financial": {"finding_1"}},
            evidence_by_finding={
                "finding_1": {"evidence_1"},
                "finding_2": {"evidence_2"},
            },
            verification_status_by_finding={"finding_1": "supported"},
        )


def test_integrated_report_does_not_require_every_finding_in_a_theme():
    report = orchestrator.IntegratedRiskReport.model_validate(
        {
            "title": "IPO 风险报告",
            "executive_summary": "存在风险",
            "overall_risk_level": "中",
            "sections": [
                {
                    "module_id": "financial",
                    "title": "财务风险",
                    "analysis": "现金流承压",
                    "finding_ids": ["finding_1", "finding_2"],
                    "evidence_ids": ["evidence_1", "evidence_2"],
                }
            ],
            "risk_themes": [
                {
                    "title": "现金流风险",
                    "conclusion": "现金流承压。",
                    "analysis": "现金消耗会削弱经营缓冲。",
                    "investment_impact": "盈利可见性下降。",
                    "finding_ids": ["finding_1"],
                    "evidence_ids": ["evidence_1"],
                }
            ],
        }
    )

    orchestrator.validate_report_references(
        report,
        module_ids={"financial"},
        finding_ids={"finding_1", "finding_2"},
        finding_ids_by_module={"financial": {"finding_1", "finding_2"}},
        evidence_by_finding={
            "finding_1": {"evidence_1"},
            "finding_2": {"evidence_2"},
        },
        verification_status_by_finding={
            "finding_1": "supported",
            "finding_2": "supported",
        },
    )


def test_integrated_report_allows_mixed_evidence_strength_in_one_theme():
    report = orchestrator.IntegratedRiskReport.model_validate(
        {
            "title": "IPO 风险报告",
            "executive_summary": "存在风险",
            "overall_risk_level": "中",
            "sections": [
                {
                    "module_id": "financial",
                    "title": "财务风险",
                    "analysis": "现金流与收入质量均待核验",
                    "finding_ids": ["finding_1", "finding_2"],
                    "evidence_ids": ["evidence_1", "evidence_2"],
                }
            ],
            "risk_themes": [
                {
                    "title": "经营质量风险",
                    "conclusion": "经营质量承压。",
                    "analysis": "现金流与收入质量可能共同影响盈利兑现。",
                    "investment_impact": "盈利可见性下降。",
                    "finding_ids": ["finding_1", "finding_2"],
                    "evidence_ids": ["evidence_1", "evidence_2"],
                }
            ],
        }
    )

    orchestrator.validate_report_references(
        report,
        module_ids={"financial"},
        finding_ids={"finding_1", "finding_2"},
        finding_ids_by_module={"financial": {"finding_1", "finding_2"}},
        evidence_by_finding={
            "finding_1": {"evidence_1"},
            "finding_2": {"evidence_2"},
        },
        verification_status_by_finding={
            "finding_1": "supported",
            "finding_2": "weakly_supported",
        },
    )


def test_integrated_report_rejects_finding_from_another_module():
    report = orchestrator.IntegratedRiskReport.model_validate(
        {
            "title": "IPO 风险报告",
            "executive_summary": "存在风险",
            "overall_risk_level": "中",
            "sections": [
                {
                    "module_id": "financial",
                    "title": "财务风险",
                    "analysis": "错误引用法务模块判断",
                    "finding_ids": ["finding_legal"],
                    "evidence_ids": ["evidence_legal"],
                },
                {
                    "module_id": "legal",
                    "title": "法务风险",
                    "analysis": "未发现重大风险",
                },
            ],
            "risk_themes": [
                {
                    "title": "法务风险",
                    "conclusion": "存在法务风险。",
                    "analysis": "相关事项会增加合规不确定性。",
                    "investment_impact": "经营兑现可能受到约束。",
                    "finding_ids": ["finding_legal"],
                    "evidence_ids": ["evidence_legal"],
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="another module"):
        orchestrator.validate_report_references(
            report,
            module_ids={"financial", "legal"},
            finding_ids={"finding_legal"},
            finding_ids_by_module={
                "financial": set(),
                "legal": {"finding_legal"},
            },
            evidence_by_finding={"finding_legal": {"evidence_legal"}},
            verification_status_by_finding={"finding_legal": "supported"},
        )
