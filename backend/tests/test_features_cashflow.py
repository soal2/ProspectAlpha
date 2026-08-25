"""Cashflow extraction contract tests (features.financial).

Anti-fabrication is the core contract: matched rows only, same-block pairing,
and an empty extraction when nothing matches.
"""

from __future__ import annotations

from dataclasses import dataclass

from riskagent.features.financial import extract_cashflow_periods


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
