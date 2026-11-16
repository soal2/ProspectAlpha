"""Cashflow extraction contract tests (features.financial).

Anti-fabrication is the core contract: matched rows only, same-block pairing,
and an empty extraction when nothing matches.
"""

from __future__ import annotations

from dataclasses import dataclass

from riskagent.features.financial import extract_cashflow_periods
from riskagent.skill_runtime import CashflowAnalyzerHandler, SkillInvocation


@dataclass
class _Block:
    block_id: str
    page_number: int
    section_path: str
    block_type: str
    text: str


_SIMPLIFIED_TABLE = _Block(
    block_id="block_t1",
    page_number=196,
    section_path="财务资料",
    block_type="table",
    text=(
        "现金及现金等价物（附注3） 1,234,567 987,654 456,789\n"
        "经营活动产生的现金流量净额 (234,567) (198,765) (176,543)\n"
    ),
)

_TRADITIONAL_HTML_TABLE = _Block(
    block_id="block_t2",
    page_number=201,
    section_path="財務資料",
    block_type="table_html",
    text=(
        "<table><tr><td>現金及現金等價物</td><td>890,123</td><td>756,789</td></tr>"
        "<tr><td>經營活動所用現金淨額</td><td>(150,234)</td><td>(140,111)</td></tr></table>"
    ),
)


def test_extracts_periods_from_simplified_table_with_note_reference():
    result = extract_cashflow_periods([_SIMPLIFIED_TABLE])

    assert result
    assert len(result.periods) == 3
    assert result.periods[0]["cash_and_equivalents"] == 1234567.0
    assert result.periods[0]["operating_cashflow"] == -234567.0
    assert result.evidence_refs == ["block_t1"]
    assert result.periods[0]["evidence_page_number"] == 196
    # The note reference "(3)" in the row label must not leak into values.
    assert all(period["cash_and_equivalents"] > 1000 for period in result.periods)


def test_extracts_periods_from_traditional_html_table():
    result = extract_cashflow_periods([_TRADITIONAL_HTML_TABLE])

    assert result
    assert len(result.periods) == 2
    assert result.periods[0]["cash_and_equivalents"] == 890123.0
    assert result.periods[0]["operating_cashflow"] == -150234.0


def test_prefers_table_blocks_over_text_blocks():
    text_block = _Block(
        block_id="block_text",
        page_number=10,
        section_path="概要",
        block_type="text",
        text="现金及现金等价物为 500 经营活动产生的现金流量净额为 -50",
    )
    result = extract_cashflow_periods([_SIMPLIFIED_TABLE, text_block])

    assert result.evidence_refs == ["block_t1"]


def test_returns_empty_when_nothing_matches():
    block = _Block(
        block_id="block_x",
        page_number=5,
        section_path="业务",
        block_type="text",
        text="公司业务稳健，收入持续增长 123 456。",
    )

    assert not extract_cashflow_periods([block])


_REVERSED_ANNUAL = _Block(
    block_id="block_reversed",
    page_number=12,
    section_path="财务资料",
    block_type="table",
    text=(
        "截至12月31日止年度\n"
        "2024年 2023年 2022年\n"
        "人民币千元\n"
        "现金及现金等价物 300 200 100\n"
        "经营活动产生的现金流量净额 (30) (20) (60)\n"
    ),
)

_INTERIM_COMPARATIVE = _Block(
    block_id="block_interim",
    page_number=18,
    section_path="财务资料",
    block_type="table_html",
    text=(
        "<table>"
        "<tr><td></td><td>截至12月31日止年度</td><td>截至6月30日止六个月</td></tr>"
        "<tr><td></td><td>2023年</td><td>2024年</td></tr>"
        "<tr><td>人民币千元</td><td></td><td></td></tr>"
        "<tr><td>现金及现金等价物</td><td>120</td><td>30</td></tr>"
        "<tr><td>经营活动产生的现金流量净额</td><td>(40)</td><td>(10)</td></tr>"
        "</table>"
    ),
)

_ZERO_CASH = _Block(
    block_id="block_zero",
    page_number=21,
    section_path="财务资料",
    block_type="table",
    text=(
        "截至12月31日止年度\n"
        "2024年\n"
        "人民币千元\n"
        "现金及现金等价物 0\n"
        "经营活动产生的现金流量净额 (12)\n"
    ),
)

_UNIT_CONFLICT = _Block(
    block_id="block_unit",
    page_number=22,
    section_path="财务资料",
    block_type="table",
    text=(
        "截至12月31日止年度\n"
        "2024年\n"
        "现金及现金等价物（人民币千元） 1000\n"
        "经营活动产生的现金流量净额（人民币万元） (10)\n"
    ),
)


def test_reversed_annual_columns_keep_period_end_and_zero_is_retained():
    result = extract_cashflow_periods([_REVERSED_ANNUAL])

    assert [item["period_end"] for item in result.periods] == ["2024-12-31", "2023-12-31", "2022-12-31"]
    assert {item["duration_months"] for item in result.periods} == {12}
    assert result.currency == "CNY"
    assert result.unit == "千元"
    assert result.unit_consistent is True
    assert result.periods[0]["evidence_block_id"] == "block_reversed"
    assert result.periods[0]["evidence_page_number"] == 12
    assert result.periods[0]["evidence_section_path"] == "财务资料"


def test_interim_comparative_columns_keep_their_own_duration():
    result = extract_cashflow_periods([_INTERIM_COMPARATIVE])

    assert [(item["period_end"], item["duration_months"], item["cash_and_equivalents"]) for item in result.periods] == [
        ("2023-12-31", 12, 120.0),
        ("2024-06-30", 6, 30.0),
    ]
    assert result.unit == "千元"
    assert result.currency == "CNY"


def test_zero_cash_period_is_kept():
    result = extract_cashflow_periods([_ZERO_CASH])

    assert len(result.periods) == 1
    assert result.periods[0]["cash_and_equivalents"] == 0
    assert result.periods[0]["operating_cashflow"] == -12
    assert result.periods[0]["ambiguous"] is False


def test_conflicting_units_are_rejected_with_provenance():
    result = extract_cashflow_periods([_UNIT_CONFLICT])

    assert result.evidence_refs == ["block_unit"]
    assert result.unit_consistent is False
    assert result.periods[0]["ambiguous"] is True
    assert any("单位" in item for item in result.limitations)
    assert result.matched_rows[0]["block_id"] == "block_unit"
    assert result.matched_rows[0]["page_number"] == 22


def _cashflow_result(block):
    extraction = extract_cashflow_periods([block])
    invocation = SkillInvocation.model_validate(
        {
            "run_id": "run_cash",
            "assignment_id": "module_cash",
            "agent_name": "qin_bingwen_financial_agent",
            "skill_id": "cashflow-analyzer",
            "skill_version": "1.0.0",
            "company_name": "测试公司",
            "structured_inputs": {
                "cashflow_periods": extraction.periods,
                "cashflow_evidence_refs": extraction.evidence_refs,
                "cashflow_limitations": extraction.limitations,
            },
        }
    )
    return extraction, CashflowAnalyzerHandler().invoke(invocation)


def test_runway_uses_latest_dated_period_and_its_duration():
    _extraction, reversed_result = _cashflow_result(_REVERSED_ANNUAL)
    assert reversed_result.status == "completed"
    assert reversed_result.result["latest_period_end"] == "2024-12-31"
    assert reversed_result.result["duration_months"] == 12
    assert reversed_result.result["cash_runway_months"] == 120
    assert reversed_result.result["unit"] == "千元"
    assert reversed_result.result["currency"] == "CNY"
    assert reversed_result.evidence_refs == ["block_reversed"]

    _interim, interim_result = _cashflow_result(_INTERIM_COMPARATIVE)
    assert interim_result.result["latest_period_end"] == "2024-06-30"
    assert interim_result.result["duration_months"] == 6
    assert interim_result.result["cash_runway_months"] == 18


def test_zero_cash_runway_is_zero_and_ambiguous_periods_have_no_runway():
    _zero, zero_result = _cashflow_result(_ZERO_CASH)
    assert zero_result.result["latest_cash_and_equivalents"] == 0
    assert zero_result.result["cash_runway_months"] == 0

    _ambiguous, ambiguous_result = _cashflow_result(_SIMPLIFIED_TABLE)
    assert ambiguous_result.status == "insufficient_data"
    assert "cash_runway_months" not in ambiguous_result.result

    _conflict, conflict_result = _cashflow_result(_UNIT_CONFLICT)
    assert conflict_result.status == "insufficient_data"
    assert "cash_runway_months" not in conflict_result.result
    assert conflict_result.evidence_refs == ["block_unit"]
    assert any("单位" in item for item in conflict_result.limitations)


def test_returns_empty_when_rows_are_in_different_blocks():
    """Cash and operating rows must come from the same table (same unit)."""
    cash_only = _Block(
        block_id="block_c",
        page_number=1,
        section_path="财务资料",
        block_type="table",
        text="现金及现金等价物 100 200\n",
    )
    operating_only = _Block(
        block_id="block_o",
        page_number=9,
        section_path="财务资料",
        block_type="table",
        text="经营活动产生的现金流量净额 (30) (40)\n",
    )

    assert not extract_cashflow_periods([cash_only, operating_only])
