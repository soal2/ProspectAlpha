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
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
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
# A bare parenthetical amount such as "(30)" is a value, not a note.
_NOTE_REF_RE = re.compile(
    r"[（(]\s*(?:附注|附註|注|註)\s*\d+(?:\.\d+)?(?:[a-z])?\s*[)）]",
    re.IGNORECASE,
)
_PERIOD_CAPTION_RE = re.compile(
    r"截至\s*(?:(?P<year>20\d{2}|19\d{2})\s*年\s*)?"
    r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日止\s*"
    r"(?P<span>年度|十二个?月|十二個月|12个月|六个月|六個月|6个月|三个月|三個月|3个月|九个月|九個月|9个月)"
)
_YEAR_RE = re.compile(r"(20\d{2}|19\d{2})\s*年")
_MONTH_SPAN_RE = re.compile(
    r"(20\d{2}|19\d{2})\s*年\s*(\d{1,2})\s*[-–至]\s*(\d{1,2})\s*月"
)
_UNIT_RE = re.compile(
    r"(人民币|人民幣|港币|港幣|港元|美元|RMB|CNY|HKD|HK\$|US\$|USD)?"
    r"\s*(千港元|千元|万元|萬元|百万元|百萬元|亿元|億元|'000)"
)
_SPAN_MONTHS = {
    "年度": 12,
    "十二个月": 12,
    "十二個月": 12,
    "12个月": 12,
    "六个月": 6,
    "六個月": 6,
    "6个月": 6,
    "三个月": 3,
    "三個月": 3,
    "3个月": 3,
    "九个月": 9,
    "九個月": 9,
    "9个月": 9,
}
_CURRENCY = {
    "人民币": "CNY",
    "人民幣": "CNY",
    "RMB": "CNY",
    "CNY": "CNY",
    "港币": "HKD",
    "港幣": "HKD",
    "港元": "HKD",
    "HKD": "HKD",
    "HK$": "HKD",
    "美元": "USD",
    "USD": "USD",
    "US$": "USD",
}


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
    limitations: list[str] = field(default_factory=list)
    currency: str | None = None
    unit: str | None = None
    unit_consistent: bool = True

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


def _span_months(span: str) -> int | None:
    return _SPAN_MONTHS.get(span)


def _period_end(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _caption_spec(match: re.Match[str], *, year: int | None = None) -> dict[str, Any] | None:
    months = _span_months(match.group("span"))
    if months is None:
        return None
    resolved_year = year if year is not None else int(match.group("year") or 0)
    if resolved_year <= 0:
        return None
    ending = _period_end(resolved_year, int(match.group("month")), int(match.group("day")))
    if ending is None:
        return None
    return {"period_end": ending, "duration_months": months}


def _interpret_column_headers(parts: list[str]) -> dict[str, Any] | None:
    text = " ".join(part for part in parts if part and part.strip())
    if not text.strip():
        return None
    span = _MONTH_SPAN_RE.search(text)
    if span:
        year = int(span.group(1))
        start_month = int(span.group(2))
        end_month = int(span.group(3))
        duration = end_month - start_month + 1
        if duration not in {3, 6, 9, 12} or not 1 <= start_month <= 12 or not 1 <= end_month <= 12:
            return None
        ending = _period_end(year, end_month, monthrange(year, end_month)[1])
        if ending is None:
            return None
        return {"period_end": ending, "duration_months": duration}
    caption = _PERIOD_CAPTION_RE.search(text)
    if caption is None:
        return None
    year_text = caption.group("year")
    if not year_text:
        years = [int(item) for item in _YEAR_RE.findall(text)]
        if len(years) != 1:
            return None
        year_text = str(years[0])
    return _caption_spec(caption, year=int(year_text))


def _html_grid(text: str) -> list[list[str]] | None:
    row_htmls = re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.IGNORECASE | re.DOTALL)
    if not row_htmls:
        return None
    grid: list[list[str]] = []
    for row_html in row_htmls:
        cells: list[str] = []
        for match in re.finditer(
            r"<t[dh]([^>]*)>(.*?)</t[dh]>",
            row_html,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            attrs, inner = match.group(1), match.group(2)
            span_match = re.search(r"colspan\s*=\s*['\"]?(\d+)", attrs, flags=re.IGNORECASE)
            span = int(span_match.group(1)) if span_match else 1
            cells.extend([_normalize_line(inner)] * max(span, 1))
        if cells:
            grid.append(cells)
    return grid or None


def _pipe_grid(text: str) -> list[list[str]] | None:
    lines = [line for line in text.splitlines() if "|" in line]
    if len(lines) < 2:
        return None
    return [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]


def _value_cells(row: list[str], column_count: int) -> list[str] | None:
    if len(row) == column_count:
        return row
    if len(row) == column_count + 1:
        return row[1:]
    return None


def _specs_from_grid(rows: list[list[str]], column_count: int) -> list[dict[str, Any]] | None:
    label_index = None
    for index, row in enumerate(rows):
        joined = " ".join(row)
        if _label_end(joined, _CASH_LABELS + _OPERATING_LABELS) is not None:
            label_index = index
            break
    if label_index is None:
        return None
    headers: list[list[str]] = [[] for _ in range(column_count)]
    for row in rows[:label_index]:
        cells = _value_cells(row, column_count)
        if cells is None:
            continue
        for index, cell in enumerate(cells):
            if cell.strip():
                headers[index].append(cell.strip())
    if not any(headers):
        return None
    specs: list[dict[str, Any]] = []
    for column in headers:
        spec = _interpret_column_headers(column)
        if spec is None:
            return None
        specs.append(spec)
    return specs


def _loose_period_specs(text: str, column_count: int) -> list[dict[str, Any]] | None:
    header_lines: list[str] = []
    for raw_line in text.splitlines():
        line = _normalize_line(raw_line)
        if _label_end(line, _CASH_LABELS + _OPERATING_LABELS) is not None:
            break
        if line:
            header_lines.append(line)
    header = "\n".join(header_lines)
    captions = list(_PERIOD_CAPTION_RE.finditer(header))
    year_line = ""
    year_matches: list[re.Match[str]] = []
    for line in header_lines:
        matches = list(_YEAR_RE.finditer(line))
        if len(matches) == column_count:
            year_line = line
            year_matches = matches
            break
    if year_matches and len(captions) == 1 and captions[0].group("year") is None:
        specs = []
        for match in year_matches:
            spec = _caption_spec(captions[0], year=int(match.group(1)))
            if spec is None:
                return None
            specs.append(spec)
        return specs
    if year_matches and len(captions) > 1:
        ordered = sorted(captions, key=lambda item: item.start())
        grouped: dict[int, list[int]] = {index: [] for index in range(len(ordered))}
        for match in year_matches:
            owner = None
            for index, caption in enumerate(ordered):
                if caption.start() <= match.start():
                    owner = index
            if owner is None:
                return None
            grouped[owner].append(int(match.group(1)))
        specs = []
        for index, caption in enumerate(ordered):
            years = grouped[index]
            if not years:
                return None
            for year in years:
                spec = _caption_spec(caption, year=year)
                if spec is None:
                    return None
                specs.append(spec)
        return specs if len(specs) == column_count else None
    full = [item for item in captions if item.group("year")]
    if len(full) == column_count:
        specs = []
        for caption in full:
            spec = _caption_spec(caption, year=int(caption.group("year")))
            if spec is None:
                return None
            specs.append(spec)
        return specs
    return None


def _period_specs(text: str, block_type: str, column_count: int) -> list[dict[str, Any]] | None:
    if column_count <= 0:
        return None
    grid = _html_grid(text) if block_type == "table_html" else None
    if grid is None and "|" in text:
        grid = _pipe_grid(text)
    if grid is not None:
        specs = _specs_from_grid(grid, column_count)
        if specs is not None:
            return specs
    return _loose_period_specs(text, column_count)


def _unit_mentions(text: str) -> list[tuple[str | None, str]]:
    mentions: list[tuple[str | None, str]] = []
    for match in _UNIT_RE.finditer(text):
        currency = _CURRENCY.get(match.group(1) or "")
        unit = match.group(2)
        if unit == "'000":
            unit = "千元"
        mentions.append((currency, unit))
    return mentions


def _unit_metadata(text: str) -> tuple[str | None, str | None, bool, str | None]:
    mentions = _unit_mentions(text)
    if not mentions:
        return None, None, True, None
    currencies = {currency for currency, _unit in mentions if currency}
    units = {unit for _currency, unit in mentions}
    if len(currencies) > 1 or len(units) > 1:
        observed = "、".join(sorted({f"{currency or '未标明币种'}{unit}" for currency, unit in mentions}))
        return None, None, False, f"现金与经营现金流单位不一致（{observed}），已拒绝计算跑道。"
    return next(iter(currencies), None), next(iter(units)), True, None


def extract_cashflow_periods(blocks: list[ProspectusBlock]) -> CashflowExtraction:
    """Extract multi-period cash and operating-cashflow rows from prospectus blocks.

    Periods stay in printed column order and carry period_end, duration_months,
    unit, currency, and the source block when those facts can be read. A column
    without a parseable period is marked ambiguous. Zero cash is kept. Conflicting
    units are recorded and must not be turned into a runway.
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

    block = next(item for item in blocks if str(item.block_id) == block_id)
    block_text = str(getattr(block, "text", "") or "")
    block_type = str(getattr(block, "block_type", "text") or "text").lower()
    specs = _period_specs(block_text, block_type, column_count)
    currency, unit, unit_consistent, unit_limitation = _unit_metadata(
        "\n".join(
            [
                block_text,
                cash_row.label_text,
                operating_row.label_text,
            ]
        )
    )
    limitations = [unit_limitation] if unit_limitation else []
    periods: list[dict[str, Any]] = []
    for index in range(column_count):
        spec = specs[index] if specs is not None else None
        periods.append(
            {
                "column_index": index,
                "period_end": None if spec is None else spec["period_end"],
                "duration_months": None if spec is None else spec["duration_months"],
                "ambiguous": spec is None or not unit_consistent,
                "unit": unit,
                "currency": currency,
                "unit_consistent": unit_consistent,
                "cash_and_equivalents": cash_row.values[index],
                "operating_cashflow": operating_row.values[index],
                "evidence_block_id": block_id,
                "evidence_page_number": cash_row.page_number,
                "evidence_section_path": cash_row.section_path,
            }
        )
    return CashflowExtraction(
        periods=periods,
        evidence_refs=[block_id],
        limitations=limitations,
        currency=currency,
        unit=unit,
        unit_consistent=unit_consistent,
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
