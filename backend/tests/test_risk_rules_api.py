import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from riskagent.api.main import create_app
from riskagent.database import make_engine
from riskagent.risk_rules import RiskRuleRepository


def test_risk_rules_api_crud(tmp_path, monkeypatch):
    src = Path(__file__).parents[1] / "configs" / "hk_ipo_risk_rules_v1.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    # The seed workbook evolves; derive the expectation from the seed itself so
    # rule-library growth does not turn into test drift.
    expected_rule_count = len(data["rules"])
    store = tmp_path / "risk_rules.yaml"
    RiskRuleRepository(store)._write_data(data)
    monkeypatch.setenv("RISK_RULES_PATH", str(store))

    client = TestClient(create_app())

    response = client.get("/risk-rules")
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["rule_count"] == expected_rule_count

    response = client.post(
        "/risk-rules",
        json={
            "dimension_code": "D2",
            "dimension": "维度2：非标隐性风险",
            "rule_type": "qualitative_disclosure",
            "category": "测试分类",
            "risk_item": "测试 API 风险",
            "description": "测试描述",
            "trigger_condition": "测试阈值",
            "calculation_or_method": "测试方法",
            "source_sections": "测试章节",
        },
    )
    assert response.status_code == 201
    rule_id = response.json()["rule_id"]

    response = client.patch(f"/risk-rules/{rule_id}", json={"risk_item": "已更新 API 风险"})
    assert response.status_code == 200
    assert response.json()["risk_item"] == "已更新 API 风险"

    response = client.delete(f"/risk-rules/{rule_id}")
    assert response.status_code == 204
    assert client.get("/risk-rules").json()["totals"]["rule_count"] == expected_rule_count


def test_risk_rules_repository_seeds_empty_database(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'rules.db'}")
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    repo = RiskRuleRepository(session_factory=session_factory)

    rule_set = repo.load()

    src = Path(__file__).parents[1] / "configs" / "hk_ipo_risk_rules_v1.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    assert rule_set.rule_set_id == "hk_ipo_prospectus_risk_framework_v1"
    assert rule_set.totals == {
        "dimension_count": len(data["dimensions"]),
        "category_count": len({item["category"] for item in data["rules"]}),
        "rule_count": len(data["rules"]),
    }


def test_risk_rules_api_missing_rule_returns_404(tmp_path, monkeypatch):
    src = Path(__file__).parents[1] / "configs" / "hk_ipo_risk_rules_v1.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    store = tmp_path / "risk_rules.yaml"
    RiskRuleRepository(store)._write_data(data)
    monkeypatch.setenv("RISK_RULES_PATH", str(store))

    client = TestClient(create_app())

    assert client.get("/risk-rules/HKIPO-999").status_code == 404
    assert client.patch("/risk-rules/HKIPO-999", json={"risk_item": "x"}).status_code == 404
    assert client.delete("/risk-rules/HKIPO-999").status_code == 404

    response = client.get("/risk-rules/HKIPO-001")
    assert response.status_code == 200
    assert response.json()["rule_id"] == "HKIPO-001"


def test_risk_rules_api_enabled_toggle_persists(tmp_path, monkeypatch):
    src = Path(__file__).parents[1] / "configs" / "hk_ipo_risk_rules_v1.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    store = tmp_path / "risk_rules.yaml"
    RiskRuleRepository(store)._write_data(data)
    monkeypatch.setenv("RISK_RULES_PATH", str(store))

    client = TestClient(create_app())

    response = client.patch("/risk-rules/HKIPO-001", json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["enabled"] is False

    fetched = client.get("/risk-rules/HKIPO-001").json()
    assert fetched["enabled"] is False

    ruleset = client.get("/risk-rules").json()
    first = next(rule for rule in ruleset["rules"] if rule["rule_id"] == "HKIPO-001")
    assert first["enabled"] is False
