import json
from pathlib import Path

from riskagent.risk_rules import RiskRuleCreate, RiskRuleRepository, RiskRuleUpdate
from riskagent.skills.risk_extract import evaluate_text_against_rules


def _repo(tmp_path: Path) -> RiskRuleRepository:
    src = Path(__file__).parents[1] / "configs" / "hk_ipo_risk_rules_v1.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    path = tmp_path / "risk_rules.yaml"
    repo = RiskRuleRepository(path)
    repo._write_data(data)
    return repo


def test_repository_loads_bootstrap_rules(tmp_path):
    repo = _repo(tmp_path)
    rule_set = repo.load()

    assert rule_set.totals == {"dimension_count": 3, "category_count": 26, "rule_count": 143}
    assert rule_set.rules[0].rule_id == "HKIPO-001"


def test_repository_crud_recalculates_totals(tmp_path):
    repo = _repo(tmp_path)
    created = repo.create_rule(
        RiskRuleCreate(
            dimension_code="D1",
            dimension="维度1：标准化财务指标风险",
            rule_type="quantitative_financial",
            category="测试分类",
            risk_item="测试风险",
            description="测试描述",
            trigger_condition="测试阈值",
            calculation_or_method="测试方法",
            source_sections="测试章节",
        )
    )

    assert created.rule_id == "HKIPO-144"
    assert repo.load().totals["rule_count"] == 144

    updated = repo.update_rule(created.rule_id, RiskRuleUpdate(risk_item="更新风险", enabled=False))
    assert updated.risk_item == "更新风险"
    assert updated.enabled is False

    repo.delete_rule(created.rule_id)
    assert repo.load().totals["rule_count"] == 143


def test_agent_text_evaluation_uses_rules(tmp_path):
    repo = _repo(tmp_path)
    rules = repo.select_rules(category="关联交易")
    findings = evaluate_text_against_rules("公司存在关联交易及持续采购安排。", rules=rules, repository=repo)

    assert findings
    assert any(item.evaluation_result == "candidate" for item in findings)
    assert all(item.evaluation_result != "triggered" for item in findings)
    assert all(item.rule_set_id == "hk_ipo_prospectus_risk_framework_v1" for item in findings)


def test_generic_major_word_does_not_trigger_unrelated_rules(tmp_path):
    repo = _repo(tmp_path)
    rules = repo.select_rules(category="持续经营")

    findings = evaluate_text_against_rules(
        "本次交易构成重大收购，相关承销安排详见封面。",
        rules=rules,
        repository=repo,
    )

    assert findings
    assert all(item.evaluation_result == "not_evaluated" for item in findings)
    assert all("重大" not in item.matched_terms for item in findings)
