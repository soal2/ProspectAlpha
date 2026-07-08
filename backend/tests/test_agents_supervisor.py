from pathlib import Path

from riskagent.agents.supervisor import AgentTeamSupervisor
from riskagent.risk_rules import RiskRuleRepository


def test_agent_supervisor_uses_langgraph_and_emits_team_events():
    repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    supervisor = AgentTeamSupervisor(repository=repo)

    result = supervisor.run(
        text="公司存在关联交易、赎回安排，经营活动现金流持续为负。",
        max_findings=10,
    )

    assert hasattr(supervisor.graph, "invoke")
    assert result.evaluations
    actor_names = {event["actor_name"] for event in result.events}
    assert actor_names.issuperset(
        {"orchestrator", "financial_agent", "legal_agent", "business_agent", "market_agent"}
    )
    assert any("LangGraph supervisor" in event["title"] for event in result.events)
