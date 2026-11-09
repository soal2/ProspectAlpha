"""Pin the hidden-first scorecard outputs before the prediction refactor.

Expected numbers were captured from ``calculate_hidden_first_score`` while it
still lived in ``backtesting/service.py``. The dicts below are the contract:
total score, risk level, bucket breakdown, ablation fields, limitations, and
the scoring id must stay identical after the move.
"""

from __future__ import annotations

from typing import Any

from riskagent.backtesting.service import (
    SCORECARD_VERSION,
    WEIGHT_PROFILE,
    calculate_hidden_first_score,
)

EXT_LIMITATION = "未取得上市前独立外部共振证据；EXT 贡献为 0，不按中性值填补。"

# Reconstructed from the 02631 天岳先进 excerpt run described in the environment
# notes (run_4b6aee2f41e6, offline card ipo-risk-v2-hidden-first). The run
# artifact itself is not in the repo. The nine themes, the 3 high / 5 medium /
# 1 low severity mix, and the published 44.29 / medium result are reproduced
# with the current factor rules:
#   D1 two direct highs (ASP, utilization) -> 10.00
#   D2 one direct high + one direct medium (supplier, talent) -> 16.70
#   D3 three inferential mediums in the top-3 cap (milestone, vague wording,
#   sentiment); the low narrative does not enter the cap -> 17.59
#   EXT tariff finding is reported but not corroborated -> 0
#   10.00 + 16.70 + 17.59 + 0 = 44.29, which maps to medium.
TIANYUE_02631_FINDINGS: list[dict[str, Any]] = [
    {
        "rule_id": "HKIPO-ASP",
        "title": "ASP 连续下滑",
        "dimension_code": "D1",
        "origin": "rule",
        "severity": "high",
        "evidence_grade": "direct",
        "confidence": 0.22,
    },
    {
        "rule_id": "HKIPO-UTIL",
        "title": "产能利用率骤降",
        "dimension_code": "D1",
        "origin": "rule",
        "severity": "high",
        "evidence_grade": "direct",
        "confidence": 0.91,
    },
    {
        "rule_id": "HKIPO-SUPPLIER",
        "title": "供应商集中",
        "dimension_code": "D2",
        "origin": "rule",
        "severity": "high",
        "evidence_grade": "direct",
        "confidence": 0.4,
    },
    {
        "rule_id": "HKIPO-TALENT",
        "title": "人才依赖",
        "dimension_code": "D2",
        "origin": "rule",
        "severity": "medium",
        "evidence_grade": "direct",
        "confidence": 0.55,
    },
    {
        "rule_id": "TEXT-MILESTONE",
        "title": "12 英寸象征性里程碑",
        "dimension_code": "D3",
        "origin": "text_signal",
        "severity": "medium",
        "evidence_grade": "inferential",
        "confidence": 0.33,
    },
    {
        "rule_id": "TEXT-VAGUE",
        "title": "模糊措辞密度",
        "dimension_code": "D3",
        "origin": "text_signal",
        "severity": "medium",
        "evidence_grade": "inferential",
        "confidence": 0.37,
    },
    {
        "rule_id": "TEXT-SENTIMENT",
        "title": "情绪不对称",
        "dimension_code": "D3",
        "origin": "text_signal",
        "severity": "medium",
        "evidence_grade": "inferential",
        "confidence": 0.28,
    },
    {
        "rule_id": "EXT-TARIFF-301",
        "title": "美对华半导体关税/301",
        "dimension_code": "EXT",
        "origin": "external",
        "severity": "medium",
        "evidence_grade": "lead",
        "hypothesis_status": "reported",
        "external_evidence_ids": [],
        "confidence": 0.15,
    },
    {
        "rule_id": "TEXT-NARRATIVE",
        "title": "新兴应用叙事 vs 车市价格战",
        "dimension_code": "D3",
        "origin": "text_signal",
        "severity": "low",
        "evidence_grade": "inferential",
        "confidence": 0.61,
    },
]


def _score(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return calculate_hidden_first_score(findings)


def test_regression_pins_match_current_scorecard() -> None:
    cases: list[tuple[str, list[dict[str, Any]], dict[str, Any]]] = [
        (
            "empty",
            [],
            {
                "score": 0.0,
                "risk_level": "very_low",
                "breakdown": {
                    "D1": 0.0,
                    "D2": 0.0,
                    "D3": 0.0,
                    "EXT": 0.0,
                    "baseline_d1": 0.0,
                    "without_d2": 0.0,
                    "without_d3": 0.0,
                    "without_ext": 0.0,
                },
                "limitations": [EXT_LIMITATION],
                "scorecard_version": "ipo-risk-v2-hidden-first",
                "weight_profile": {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0},
            },
        ),
        (
            "one_hit_per_bucket_ext_corroborated",
            [
                {"dimension_code": "D1", "severity": "high", "evidence_grade": "direct", "confidence": 0.01},
                {"dimension_code": "D2", "severity": "high", "evidence_grade": "direct", "confidence": 0.01},
                {"dimension_code": "D3", "severity": "high", "evidence_grade": "direct", "confidence": 0.01},
                {
                    "dimension_code": "EXT",
                    "severity": "low",
                    "evidence_grade": "lead",
                    "confidence": 0.01,
                    "hypothesis_status": "corroborated",
                },
            ],
            {
                "score": 33.34,
                "risk_level": "low",
                "breakdown": {
                    "D1": 5.0,
                    "D2": 10.0,
                    "D3": 11.67,
                    "EXT": 6.67,
                    "baseline_d1": 33.33,
                    "without_d2": 23.34,
                    "without_d3": 21.67,
                    "without_ext": 26.67,
                },
                "limitations": [],
                "scorecard_version": "ipo-risk-v2-hidden-first",
                "weight_profile": {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0},
            },
        ),
        (
            "d3_top_three_cap",
            [{"dimension_code": "D3", "severity": "high", "evidence_grade": "direct"} for _ in range(8)],
            {
                "score": 35.0,
                "risk_level": "low",
                "breakdown": {
                    "D1": 0.0,
                    "D2": 0.0,
                    "D3": 35.0,
                    "EXT": 0.0,
                    "baseline_d1": 0.0,
                    "without_d2": 35.0,
                    "without_d3": 0.0,
                    "without_ext": 35.0,
                },
                "limitations": [EXT_LIMITATION],
                "scorecard_version": "ipo-risk-v2-hidden-first",
                "weight_profile": {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0},
            },
        ),
        (
            "refuted_zero",
            [{"dimension_code": "D2", "severity": "high", "evidence_grade": "direct", "hypothesis_status": "refuted"}],
            {
                "score": 0.0,
                "risk_level": "very_low",
                "breakdown": {
                    "D1": 0.0,
                    "D2": 0.0,
                    "D3": 0.0,
                    "EXT": 0.0,
                    "baseline_d1": 0.0,
                    "without_d2": 0.0,
                    "without_d3": 0.0,
                    "without_ext": 0.0,
                },
                "limitations": [EXT_LIMITATION],
                "scorecard_version": "ipo-risk-v2-hidden-first",
                "weight_profile": {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0},
            },
        ),
        (
            "contradicted_evidence_zero",
            [{"dimension_code": "D1", "severity": "high", "evidence_grade": "contradicted"}],
            {
                "score": 0.0,
                "risk_level": "very_low",
                "breakdown": {
                    "D1": 0.0,
                    "D2": 0.0,
                    "D3": 0.0,
                    "EXT": 0.0,
                    "baseline_d1": 0.0,
                    "without_d2": 0.0,
                    "without_d3": 0.0,
                    "without_ext": 0.0,
                },
                "limitations": [EXT_LIMITATION],
                "scorecard_version": "ipo-risk-v2-hidden-first",
                "weight_profile": {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0},
            },
        ),
        (
            "financial_alias_bucket",
            [{"dimension_code": "FIN_CASHFLOW", "severity": "medium", "evidence_grade": "inferential", "origin": "rule"}],
            {
                "score": 2.51,
                "risk_level": "very_low",
                "breakdown": {
                    "D1": 2.51,
                    "D2": 0.0,
                    "D3": 0.0,
                    "EXT": 0.0,
                    "baseline_d1": 16.75,
                    "without_d2": 2.51,
                    "without_d3": 2.51,
                    "without_ext": 2.51,
                },
                "limitations": [EXT_LIMITATION],
                "scorecard_version": "ipo-risk-v2-hidden-first",
                "weight_profile": {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0},
            },
        ),
        (
            "text_signal_forced_into_d3",
            [{"dimension_code": "D1", "origin": "text_signal", "severity": "high", "evidence_grade": "direct"}],
            {
                "score": 11.67,
                "risk_level": "very_low",
                "breakdown": {
                    "D1": 0.0,
                    "D2": 0.0,
                    "D3": 11.67,
                    "EXT": 0.0,
                    "baseline_d1": 0.0,
                    "without_d2": 11.67,
                    "without_d3": 0.0,
                    "without_ext": 11.67,
                },
                "limitations": [EXT_LIMITATION],
                "scorecard_version": "ipo-risk-v2-hidden-first",
                "weight_profile": {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0},
            },
        ),
        (
            "ext_disputed",
            [{"dimension_code": "EXT", "hypothesis_status": "disputed", "severity": "high"}],
            {
                "score": 3.33,
                "risk_level": "very_low",
                "breakdown": {
                    "D1": 0.0,
                    "D2": 0.0,
                    "D3": 0.0,
                    "EXT": 3.33,
                    "baseline_d1": 0.0,
                    "without_d2": 3.33,
                    "without_d3": 3.33,
                    "without_ext": 0.0,
                },
                "limitations": [],
                "scorecard_version": "ipo-risk-v2-hidden-first",
                "weight_profile": {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0},
            },
        ),
        (
            "ext_external_and_counter_evidence",
            [
                {
                    "dimension_code": "EXT",
                    "hypothesis_status": "reported",
                    "external_evidence_ids": ["ext_1"],
                    "counter_evidence_ids": ["counter_1"],
                    "severity": "low",
                }
            ],
            {
                "score": 3.33,
                "risk_level": "very_low",
                "breakdown": {
                    "D1": 0.0,
                    "D2": 0.0,
                    "D3": 0.0,
                    "EXT": 3.33,
                    "baseline_d1": 0.0,
                    "without_d2": 3.33,
                    "without_d3": 3.33,
                    "without_ext": 0.0,
                },
                "limitations": [],
                "scorecard_version": "ipo-risk-v2-hidden-first",
                "weight_profile": {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0},
            },
        ),
        (
            "ext_corroborated_but_countered_without_external_ids",
            [{"dimension_code": "EXT", "hypothesis_status": "corroborated", "counter_evidence_ids": ["counter_1"]}],
            {
                "score": 0.0,
                "risk_level": "very_low",
                "breakdown": {
                    "D1": 0.0,
                    "D2": 0.0,
                    "D3": 0.0,
                    "EXT": 0.0,
                    "baseline_d1": 0.0,
                    "without_d2": 0.0,
                    "without_d3": 0.0,
                    "without_ext": 0.0,
                },
                "limitations": [EXT_LIMITATION],
                "scorecard_version": "ipo-risk-v2-hidden-first",
                "weight_profile": {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0},
            },
        ),
        (
            "missing_grade_defaults_to_direct",
            [{"dimension_code": "D2", "severity": "low"}],
            {
                "score": 3.3,
                "risk_level": "very_low",
                "breakdown": {
                    "D1": 0.0,
                    "D2": 3.3,
                    "D3": 0.0,
                    "EXT": 0.0,
                    "baseline_d1": 0.0,
                    "without_d2": 0.0,
                    "without_d3": 3.3,
                    "without_ext": 3.3,
                },
                "limitations": [EXT_LIMITATION],
                "scorecard_version": "ipo-risk-v2-hidden-first",
                "weight_profile": {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0},
            },
        ),
        (
            "tianyue_02631_excerpt",
            TIANYUE_02631_FINDINGS,
            {
                "score": 44.29,
                "risk_level": "medium",
                "breakdown": {
                    "D1": 10.0,
                    "D2": 16.7,
                    "D3": 17.59,
                    "EXT": 0.0,
                    "baseline_d1": 66.67,
                    "without_d2": 27.59,
                    "without_d3": 26.7,
                    "without_ext": 44.29,
                },
                "limitations": [EXT_LIMITATION],
                "scorecard_version": "ipo-risk-v2-hidden-first",
                "weight_profile": {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0},
            },
        ),
    ]

    assert SCORECARD_VERSION == "ipo-risk-v2-hidden-first"
    assert WEIGHT_PROFILE == {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0}
    for name, findings, expected in cases:
        actual = _score(findings)
        assert actual == expected, name
        shaken = [{**item, "confidence": 0.99} for item in findings]
        assert _score(shaken) == actual, name


def test_tianyue_02631_excerpt_severity_mix() -> None:
    severities = [item["severity"] for item in TIANYUE_02631_FINDINGS]
    assert len(TIANYUE_02631_FINDINGS) == 9
    assert severities.count("high") == 3
    assert severities.count("medium") == 5
    assert severities.count("low") == 1
    result = _score(TIANYUE_02631_FINDINGS)
    assert result["score"] == 44.29
    assert result["risk_level"] == "medium"
    assert result["scorecard_version"] == "ipo-risk-v2-hidden-first"
