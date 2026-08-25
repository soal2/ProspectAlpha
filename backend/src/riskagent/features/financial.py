"""Financial risk feature boundary.

Cashflow extraction from parsed prospectus blocks (tables first, then text).

Anti-fabrication rules:
- Only rows whose label matches a known cash/cashflow line item are read.
- Values are taken verbatim from the same block (same table ⇒ same unit and
  column order), so ratio metrics such as cash runway are unit-safe.
- When nothing matches, an empty extraction is returned; callers must report
  "insufficient data" instead of inventing figures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

# Simplified / traditional / English row-label variants for cash lines.
_CASH_LABELS: tuple[str, ...] = (
    "期末现金及现金等价物",
    "期末現金及現金等價物",
    "现金及现金等价物",
    "現金及現金等價物",
    "现金及等价物",
    "現金及等價物",
    "现金及银行结余",
    "現金及銀行結餘",
    "cash and cash equivalents",
)

# Row-label variants for net operating cashflow (positive or negative phrasing).
_OPERATING_LABELS: tuple[str, ...] = (
    "经营活动产生的现金流量净额",
    "经营活动产生的现金流净额",
    "经营活动产生的现金净额",
    "经营业务所得现金净额",
    "经营活动所用现金流量净额",
    "经营活动所得现金流量净额",
    "经营活动所得／(所用)现金流量净额",
    "經營活動產生的現金流量淨額",
    "經營活動產生的現金淨額",
    "經營活動所得現金淨額",
    "經營活動所用現金淨額",
    "經營活動所用現金流量淨額",
    "經營活動所得現金流量淨額",
    "經營活動所得／(所用)現金流量淨額",
    "經營活動的現金流量淨額",
    "經營業務所得現金淨額",
    "net cash generated from operating activities",
    "net cash used in operating activities",
    "net cash flows from operating activities",
)

# Flattened single-line tables put several rows on one line. A number window
# must stop at the next row label so a value is never attributed to the wrong
# row (e.g. 投資活動's numbers must not become 經營活動's).
_STOP_TERMS: tuple[str, ...] = (
    "投資活動",
    "投資活动",
    "融資活動",
    "融资活动",
    "匯率變動",
    "汇率变动",
    "期初",
    "期末",
    "增加",
    "減少",
    "减少",
    "定期存款",
    "總值",
    "总值",
    "總額",
    "总额",
    "存貨",
    "存货",
    "預付款項",
    "预付款项",
    "應收",
    "应收",
    "應付",
    "应付",
    "流動負債",
    "流动负债",
    "非流動",
    "非流动",
    "金融資產",
    "金融资产",
    "收益",
    "開支",
    "开支",
    "附註",
    "附注",
)

# Table blocks carry the numbers; text blocks are a last resort.
_TABLE_TYPES = {"table", "table_html"}
_NUMBER_RE = re.compile(r"\(?-?\s?\d[\d,]*(?:\.\d+)?\)?")
_ROW_SPLIT_RE = re.compile(r"<tr[^>]*>|\n", re.IGNORECASE)
_CELL_STRIP_RE = re.compile(r"<[^>]+>")
# Note references directly after a row label, e.g. "（附注3）" / "(注12)".
_NOTE_REF_RE = re.compile(r"[（(]\s*(?:附注|注|註)?\s*\d+(?:\.\d+)?(?:[a-z]|\（\d\))?\s*[)）]", re.IGNORECASE)


def _normalize_line(line: str) -> str:
    return _CELL_STRIP_RE.sub(" ", line).replace("|", " ").strip()


def _parse_numbers(line: str) -> list[float]:
    values: list[float] = []
    for token in _NUMBER_RE.findall(line):
        negative = token.startswith("(") and token.endswith(")")
        cleaned = token.strip("()").replace(",", "").replace(" ", "")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        values.append(-value if negative or token.startswith("-") else value)
    return values


def _label_end(line: str, labels: tuple[str, ...]) -> int | None:
    """Return the end offset of the first matching row label, else None.

    Numbers are only parsed after the label so note references like "(3)" in
    the row header can never be mistaken for a value.
    """
    lowered = line.lower()
    head = lowered[:80]
    best: int | None = None
    for label in labels:
        index = head.find(label.lower())
        if index >= 0:
            end = index + len(label)
            best = max(best, end) if best is not None else end
    return best


@dataclass
class CashflowRow:
    block_id: str
    page_number: int
    section_path: str
    values: list[float]
    label_text: str = ""


@dataclass
class CashflowExtraction:
    periods: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    matched_rows: list[dict[str, Any]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.periods)


class ProspectusBlock(Protocol):
    block_id: str
    page_number: int
    section_path: str
    block_type: str
    text: str


def _number_window(rest: str) -> str:
    """Cut the number window at the next row label (flattened-table safety)."""
    cut = len(rest)
    lowered = rest.lower()
    for term in _STOP_TERMS:
        index = lowered.find(term.lower())
        if index >= 0:
            cut = min(cut, index)
    return rest[:cut]


def _scan_blocks(
    blocks: list[ProspectusBlock], labels: tuple[str, ...]
) -> list[CashflowRow]:
    """Scan for label-matching rows, preferring table blocks over text blocks."""

    def _scan(only_tables: bool) -> list[CashflowRow]:
        rows: list[CashflowRow] = []
        for block in blocks:
            block_type = str(getattr(block, "block_type", "text") or "text").lower()
            if only_tables and block_type not in _TABLE_TYPES:
                continue
            text = str(getattr(block, "text", "") or "")
            if not text.strip():
                continue
            for raw_line in _ROW_SPLIT_RE.split(text):
                if not raw_line or not raw_line.strip():
                    continue
                line = _normalize_line(raw_line)
                span_end = _label_end(line, labels)
                if span_end is None:
                    continue
                values = _parse_numbers(
                    _number_window(_NOTE_REF_RE.sub(" ", line[span_end:]))
                )
                if not values:
                    continue
                rows.append(
                    CashflowRow(
                        block_id=str(block.block_id),
                        page_number=int(getattr(block, "page_number", 0) or 0),
                        section_path=str(getattr(block, "section_path", "") or ""),
                        values=values,
                        label_text=line[:span_end],
                    )
                )
        return rows

    return _scan(only_tables=True) or _scan(only_tables=False)


def extract_cashflow_periods(blocks: list[ProspectusBlock]) -> CashflowExtraction:
    """Extract multi-period cash & operating-cashflow rows from prospectus blocks.

    Returns periods in printed column order; each period carries the evidence
    block ids so every number stays traceable to its source block/page.
    """
    cash_rows = _scan_blocks(blocks, _CASH_LABELS)
    operating_rows = _scan_blocks(blocks, _OPERATING_LABELS)
    if not cash_rows or not operating_rows:
        return CashflowExtraction()

    # Pair rows from the same block first (same table ⇒ same unit and periods).
    cash_by_block: dict[str, CashflowRow] = {row.block_id: row for row in cash_rows}
    operating_by_block: dict[str, CashflowRow] = {
        row.block_id: row for row in operating_rows
    }
    shared = [block_id for block_id in cash_by_block if block_id in operating_by_block]
    if not shared:
        return CashflowExtraction()

    def _cash_priority(row: CashflowRow) -> int:
        # Prefer closing balance over opening balance over generic cash lines.
        if "期末" in row.label_text:
            return 2
        if "期初" in row.label_text:
            return 1
        return 0

    # Use the table with the most matched columns; among cash rows prefer
    # closing-balance rows (期末) when several blocks tie.
    block_id = max(
        shared,
        key=lambda key: (
            min(len(cash_by_block[key].values), len(operating_by_block[key].values)),
            _cash_priority(cash_by_block[key]),
        ),
    )
    cash_row = cash_by_block[block_id]
    operating_row = operating_by_block[block_id]
    column_count = min(len(cash_row.values), len(operating_row.values))
    if column_count <= 0:
        return CashflowExtraction()

    periods: list[dict[str, Any]] = []
    for index in range(column_count):
        cash_value = cash_row.values[index]
        operating_value = operating_row.values[index]
        if cash_value == 0:
            continue
        periods.append(
            {
                "column_index": index,
                "cash_and_equivalents": cash_value,
                "operating_cashflow": operating_value,
                "evidence_block_id": block_id,
                "evidence_page_number": cash_row.page_number,
            }
        )
    if not periods:
        return CashflowExtraction()
    return CashflowExtraction(
        periods=periods,
        evidence_refs=[block_id],
        matched_rows=[
            {
                "kind": "cash_and_equivalents",
                "block_id": cash_row.block_id,
                "page_number": cash_row.page_number,
                "section_path": cash_row.section_path,
                "label": cash_row.label_text,
                "values": cash_row.values,
            },
            {
                "kind": "operating_cashflow",
                "block_id": operating_row.block_id,
                "page_number": operating_row.page_number,
                "section_path": operating_row.section_path,
                "label": operating_row.label_text,
                "values": operating_row.values,
            },
        ],
    )
