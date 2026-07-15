from fastapi.testclient import TestClient

from riskagent.agents.prompts import render_expert_prompt, render_orchestrator_prompt
from riskagent.api.main import create_app
from riskagent.skill_docs import (
    AGENT_SKILL_MAP,
    load_skill_docs,
    render_skills_section,
    skills_digest,
    skills_for_agent,
)

EXPECTED_SKILLS = {
    "pdf-parse-and-trace",
    "risk-rule-screening",
    "expert-risk-analysis",
    "evidence-verification",
    "report-generation-and-archive",
}


def test_all_skill_docs_load_with_anthropic_frontmatter():
    docs = load_skill_docs()
    assert {doc.name for doc in docs} == EXPECTED_SKILLS
    for doc in docs:
        assert doc.description, doc.name
        assert doc.body.strip(), doc.name
        assert doc.path.startswith("skills/"), doc.path


def test_malformed_skill_files_are_skipped(tmp_path):
    good = tmp_path / "good-skill"
    good.mkdir()
    good.joinpath("SKILL.md").write_text(
        "---\nname: good-skill\ndescription: 有效技能\n---\n\n正文",
        encoding="utf-8",
    )
    missing_meta = tmp_path / "bad-skill"
    missing_meta.mkdir()
    missing_meta.joinpath("SKILL.md").write_text("---\nname: bad-skill\n---\n正文", encoding="utf-8")
    no_frontmatter = tmp_path / "worse-skill"
    no_frontmatter.mkdir()
    no_frontmatter.joinpath("SKILL.md").write_text("没有 frontmatter 的文件", encoding="utf-8")

    docs = load_skill_docs(tmp_path)
    assert [doc.name for doc in docs] == ["good-skill"]
    assert docs[0].body == "正文"


def test_skills_digest_lists_every_skill_within_budget():
    digest = skills_digest()
    assert len(digest) <= 2000
    for name in EXPECTED_SKILLS:
        assert name in digest


def test_agent_skill_map_resolves_full_bodies_with_expert_default():
    evidence_docs = skills_for_agent("cheng_zheng_evidence_agent")
    assert [doc.name for doc in evidence_docs] == list(
        AGENT_SKILL_MAP["cheng_zheng_evidence_agent"]
    )
    default_docs = skills_for_agent("unmapped_agent_x")
    assert [doc.name for doc in default_docs] == [
        "expert-risk-analysis",
        "evidence-verification",
    ]


def test_expert_and_orchestrator_prompts_embed_skills():
    expert = render_expert_prompt(
        {"agent_name": "qin_bingwen_financial_agent", "title": "财务尽调专家"}
    )
    assert "【技能目录】" in expert
    assert "【技能：expert-risk-analysis】" in expert
    assert "输出契约" in expert

    evidence = render_expert_prompt(
        {"agent_name": "cheng_zheng_evidence_agent", "title": "证据链专家"}
    )
    assert "【技能：pdf-parse-and-trace】" in evidence
    assert evidence != expert

    orchestrator = render_orchestrator_prompt("strategy_system", "fallback body")
    assert "【技能目录】" in orchestrator
    for name in EXPECTED_SKILLS:
        assert name in orchestrator


def test_render_skills_section_empty_when_no_skills(tmp_path):
    assert render_skills_section("any_agent", root=tmp_path / "nothing") == ""


def test_skills_api_lists_and_fetches_documents():
    client = TestClient(create_app())
    listed = client.get("/skills")
    assert listed.status_code == 200
    names = {item["name"] for item in listed.json()}
    assert names == EXPECTED_SKILLS
    assert all("body" not in item for item in listed.json())

    detail = client.get("/skills/expert-risk-analysis")
    assert detail.status_code == 200
    body = detail.json()
    assert body["name"] == "expert-risk-analysis"
    assert "输出契约" in body["body"]

    assert client.get("/skills/nonexistent").status_code == 404
