import pytest
from sqlalchemy.orm import sessionmaker

from riskagent.agent_experts import AgentExpertRepository, AgentSpec
from riskagent.database import Base, make_engine
from riskagent.models import AgentExpertRow
from riskagent.skills.risk_skill_registry import skills_for_agents


def test_agent_experts_empty_table_has_no_fallback(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    repo = AgentExpertRepository(session_factory=session_factory, ensure_schema=False)

    assert repo.list_agents() == []


def test_unknown_dynamic_expert_does_not_expand_to_fixed_default_team():
    assert skills_for_agents(["new_dynamic_expert"]) == []


def test_dynamic_agent_without_bundled_skill_still_has_complete_capability_contract():
    payload = AgentSpec(
        name="new_dynamic_expert",
        title="New dynamic expert",
        categories=("新风险域",),
    ).capability_payload(public_base_url="http://agents.test")

    assert payload["role"] == "New dynamic expert"
    assert payload["capabilities"] == ["新风险域"]
    assert payload["input_modes"] == ["application/json"]
    assert payload["output_modes"] == ["application/json"]
    assert payload["output_schema"] == "ExpertTaskOutput"
    assert payload["agent_card_url"].endswith(
        "/a2a/new_dynamic_expert/.well-known/agent-card.json"
    )


def test_dynamic_agent_name_is_safe_for_mailbox_and_a2a_routes():
    with pytest.raises(ValueError, match="agent name"):
        AgentSpec(name="unsafe/agent", title="Unsafe")


def test_agent_experts_seed_empty_database(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'agents.db'}")
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    repo = AgentExpertRepository(session_factory=session_factory)

    agents = repo.list_agents()
    assert len(agents) == 12
    assert agents[0]["id"] == "L3-001"
    assert {agent["layer"] for agent in agents} == {"L3", "L2", "L1"}


def test_agent_experts_returns_database_rows_in_display_order(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with session_factory() as session:
        session.add(
            AgentExpertRow(
                agent_id="L3-001",
                name="沈北辰",
                role="首席指挥官 · 总监",
                layer="L3",
                description="统筹全局，拆解风险维度，组建专家队、终审报告并签发。",
                expertise=["策略", "总控", "报告"],
                call_count=312,
                avg_tokens=4820,
                avatar="沈",
                accent="#0b243d",
                display_order=1,
            )
        )
        session.commit()

    repo = AgentExpertRepository(session_factory=session_factory, ensure_schema=False)

    assert repo.list_agents() == [
        {
            "id": "L3-001",
            "name": "沈北辰",
            "role": "首席指挥官 · 总监",
            "layer": "L3",
            "desc": "统筹全局，拆解风险维度，组建专家队、终审报告并签发。",
            "expertise": ["策略", "总控", "报告"],
            "callCount": 312,
            "avgTokens": 4820,
            "avatar": "沈",
            "accent": "#0b243d",
        }
    ]
