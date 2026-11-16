"""Synthetic prospectus sentences and prices.

These are not a full prospectus and contain no customer data.
"""

import json
from types import SimpleNamespace

from riskagent.features.legal import extract_legal_disclosures
from riskagent.features.market import extract_pre_ipo_market_window
from riskagent.skill_runtime import LegalComplianceHandler, SkillInvocation


def _block(block_id: str, text: str):
    return SimpleNamespace(block_id=block_id, page_number=12, section_path="风险因素", text=text)


def _invocation(**structured):
    return SkillInvocation.model_validate(
        {
            "run_id": "run_legal",
            "assignment_id": "module_legal",
            "agent_name": "wen_shouzheng_legal_agent",
            "skill_id": "legal-compliance-due-diligence",
            "skill_version": "1.0.0",
            "company_name": "合成公司",
            "data_cutoff_date": "2024-06-30",
            "structured_inputs": structured,
        }
    )


def test_legal_extraction_keeps_provenance_and_drops_future_dates() -> None:
    blocks = [
        _block("b1", "截至2024年3月31日，公司存在未决诉讼，标的额人民币100万元。"),
        _block("b2", "2025年1月2日新增赎回安排，金额港元50万元。"),
        _block("b3", "公司与关联方存在关联交易。"),
    ]
    extracted = extract_legal_disclosures(blocks, data_cutoff_date="2024-06-30")
    result = LegalComplianceHandler().invoke(
        _invocation(prospectus_blocks=[block.__dict__ for block in blocks])
    )
    blob = json.dumps(result.result, ensure_ascii=False)

    assert extracted["pre_cutoff_disclosures"][0]["kind"] == "litigation"
    assert extracted["pre_cutoff_disclosures"][0]["currency"] == "CNY"
    assert extracted["pre_cutoff_disclosures"][0]["page_number"] == 12
    assert "2025年1月2日" not in blob
    assert "新增赎回安排" not in blob
    assert result.result["post_cutoff_excluded_count"] == 1
    assert any(item["kind"] == "related_party" and item["date_status"] == "unknown" for item in result.result["undated_disclosures"])
    assert "redemption_vam" in result.result["missing_categories"]
    assert result.risk_level == "unrated"
    assert result.result["missing_is_not_low_risk"] is True
    assert result.evidence_refs == ["b1"]


def test_missing_legal_text_is_not_low_risk() -> None:
    result = LegalComplianceHandler().invoke(_invocation(prospectus_blocks=[]))

    assert result.status == "insufficient_data"
    assert result.risk_level == "unrated"
    assert result.result["missing_categories"] == ["litigation", "redemption_vam", "related_party"]


def test_market_window_uses_only_pre_cutoff_prices() -> None:
    window = extract_pre_ipo_market_window(
        [
            {"date": "2024-01-31", "close": 100, "currency": "HKD"},
            {"date": "2024-06-28", "close": 110, "currency": "HKD"},
            {"date": "2024-07-31", "close": 50, "currency": "HKD"},
            {"date": None, "close": 1, "currency": "HKD"},
        ],
        data_cutoff_date="2024-06-30",
    )

    assert window["status"] == "available"
    assert window["observations"] == 2
    assert window["trailing_return"] == 0.1
    assert window["currency"] == "HKD"
    assert window["window_end"] == "2024-06-28"


def test_sparse_or_mixed_market_data_stays_missing() -> None:
    sparse = extract_pre_ipo_market_window(
        [{"date": "2024-01-31", "close": 100, "currency": "HKD"}],
        data_cutoff_date="2024-06-30",
    )
    mixed = extract_pre_ipo_market_window(
        [
            {"date": "2024-01-31", "close": 100, "currency": "HKD"},
            {"date": "2024-02-29", "close": 90, "currency": "CNY"},
        ],
        data_cutoff_date="2024-06-30",
    )

    assert sparse["status"] == "missing"
    assert sparse["trailing_return"] is None
    assert sparse["missing_is_not_low_risk"] is True
    assert mixed["status"] == "missing"
    assert mixed["currency_consistent"] is False
