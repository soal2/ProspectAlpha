import json

import pytest
from pathlib import Path
from sqlalchemy.orm import sessionmaker

from riskagent.agent_experts import AgentExpertRepository, AgentSpec, AgentSpecRepository
from riskagent.agents.supervisor import AgentTeamSupervisor
from riskagent.database import Base, make_engine
from riskagent.models import AgentExpertRow
from riskagent.risk_rules import RiskRuleRepository
from riskagent.risk_rules import RiskRule
from riskagent.skills.risk_skill_registry import (
    rules_for_skill,
    skill_for_agent,
    skills_for_agents,
)


def test_agent_experts_empty_table_has_no_fallback(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    repo = AgentExpertRepository(session_factory=session_factory, ensure_schema=False)

    assert repo.list_agents() == []


def test_unknown_dynamic_expert_does_not_expand_to_fixed_default_team():
    assert skills_for_agents(["new_dynamic_expert"]) == []


def test_persona_skills_split_business_technology_quality_and_new_market_rules():
    seed = json.loads(
        (Path(__file__).parents[1] / "configs" / "hk_ipo_risk_rules_v1.json").read_text(
            encoding="utf-8"
        )
    )
    rules = [RiskRule.model_validate(item) for item in seed["rules"]]
    operations = skill_for_agent("su_zhiji_business_agent")
    technology = skill_for_agent("ye_zhiyuan_business_agent")
    quality = skill_for_agent("xiao_zeming_quality_agent")
    valuation = skill_for_agent("jiang_hengzhi_market_agent")
    competition = skill_for_agent("lu_guanlan_market_agent")
    legal = skill_for_agent("wen_shouzheng_legal_agent")

    assert operations and technology and quality and valuation and competition and legal
    operation_ids = {rule.rule_id for rule in rules_for_skill(operations, rules)}
    technology_ids = {rule.rule_id for rule in rules_for_skill(technology, rules)}
    quality_rules = rules_for_skill(quality, rules)
    valuation_ids = {rule.rule_id for rule in rules_for_skill(valuation, rules)}
    competition_ids = {rule.rule_id for rule in rules_for_skill(competition, rules)}
    legal_ids = {rule.rule_id for rule in rules_for_skill(legal, rules)}

    assert "HKIPO-049" in operation_ids
    assert "HKIPO-144" not in operation_ids
    assert "HKIPO-144" in technology_ids
    assert "HKIPO-049" not in technology_ids
    assert len(quality_rules) == 39
    assert {rule.rule_type for rule in quality_rules} == {"textual_nlp"}
    assert "HKIPO-146" in valuation_ids
    assert "HKIPO-145" not in valuation_ids
    assert "HKIPO-145" in competition_ids
    assert "HKIPO-146" not in competition_ids
    assert "HKIPO-147" in legal_ids


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
    assert len(agents) == 14
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


def test_persona_expert_directory_returns_14_personas_with_full_metadata(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'agents.db'}")
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    supervisor = AgentTeamSupervisor(
        repository=RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml"),
        agent_spec_repository=AgentSpecRepository(session_factory=session_factory),
        runtime_root=tmp_path / "runtime",
    )

    options = supervisor.agent_options()

    assert len(options) == 14
    required_keys = {
        "agent_name",
        "persona_id",
        "display_name",
        "layer",
        "function",
        "avatar",
        "accent",
        "expertise",
        "role_zh",
        "rule_types",
        "categories",
        "skill_id",
        "agent_card_url",
    }
    assert required_keys <= set(options[0])
    assert {option["persona_id"] for option in options} == {
        "L3-001",
        "L3-002",
        "L3-003",
        "L2-001",
        "L2-002",
        "L2-003",
        "L2-004",
        "L1-001",
        "L1-002",
        "L1-003",
        "L1-004",
        "L1-005",
        "L1-006",
        "L1-007",
    }
    first = next(option for option in options if option["persona_id"] == "L1-001")
    assert first["agent_name"] == "qin_bingwen_financial_agent"
    assert first["display_name"] == "秦秉文"
    assert first["function"] == "financial_agent"
    assert first["role_zh"]
    assert first["skill_id"] == "financial_risk_skill"
