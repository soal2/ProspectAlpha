from sqlalchemy.orm import sessionmaker

from riskagent.agent_experts import AgentExpertRepository
from riskagent.database import Base, make_engine
from riskagent.models import AgentExpertRow


def test_agent_experts_empty_table_has_no_fallback(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    repo = AgentExpertRepository(session_factory=session_factory, ensure_schema=False)

    assert repo.list_agents() == []


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
