import json
from pathlib import Path

from fastapi.testclient import TestClient

from riskagent.api.main import create_app
from riskagent.risk_rules import RiskRuleRepository


def test_risk_rules_api_crud(tmp_path, monkeypatch):
    src = Path(__file__).parents[1] / "configs" / "hk_ipo_risk_rules_v1.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    store = tmp_path / "risk_rules.yaml"
    RiskRuleRepository(store)._write_data(data)
    monkeypatch.setenv("RISK_RULES_PATH", str(store))

    client = TestClient(create_app())

    response = client.get("/risk-rules")
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["rule_count"] == 143

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
    assert client.get("/risk-rules").json()["totals"]["rule_count"] == 143
