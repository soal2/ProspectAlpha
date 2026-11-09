"""Recover prospectus tables when MinerU table recognition is turned off.

MinerU drops table blocks entirely when ``MINERU_TABLE_ENABLE`` is false, because
the table-recognition model is not loaded. Downstream cash-flow and disclosure
analysis only sees ``chunk_type="table"`` items, so this module reads the same
PDF with pdfplumber and, if that yields nothing, PyMuPDF. The original MinerU
``content_list.json`` is left unchanged; a sibling file is what the chunker reads.
"""

from __future__ import annotations

import json
import logging
import os
from html import escape
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

_DISABLED = {"0", "false", "no", "off", "disabled"}


def mineru_table_recognition_enabled() -> bool:
    """Return whether MinerU itself is expected to emit table blocks."""

    raw = os.getenv("MINERU_TABLE_ENABLE", "true").strip().lower()
    return raw not in _DISABLED


def content_list_for_chunking(*, content_list_path: Path | str, pdf_path: Path | str) -> Path:
    """Return the content list the chunker should read.

    When MinerU table recognition is enabled, or the list already contains table
    items, this is the original path. When recognition is disabled and the list
    has no tables, extracted tables are appended to a sibling JSON file.
    """

    source = Path(content_list_path).expanduser().resolve()
    if mineru_table_recognition_enabled():
        return source
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Cannot read MinerU content list for table fallback: %s", source)
        return source
    if not isinstance(payload, list):
        return source
    if any(isinstance(item, dict) and item.get("type") == "table" for item in payload):
        return source
    try:
        tables = extract_pdf_tables(Path(pdf_path))
    except Exception:
        logger.exception("PDF table fallback failed for %s", pdf_path)
        return source
    if not tables:
        logger.info("MinerU table recognition is disabled and no fallback tables were found in %s", pdf_path)
        return source
    supplemented = [*payload, *tables]
    output = source.with_name(f"{source.stem}.fallback_tables.json")
    output.write_text(json.dumps(supplemented, ensure_ascii=False), encoding="utf-8")
    logger.info("Appended %d fallback tables to %s", len(tables), output)
    return output


def extract_pdf_tables(pdf_path: Path | str) -> list[dict[str, Any]]:
    """Extract tables as MinerU-shaped content-list items.

    pdfplumber is preferred because its cell grid matches ruled prospectus
    tables. PyMuPDF is the fallback when pdfplumber is missing or finds nothing.
    """

    path = Path(pdf_path)
    for extractor in (_extract_tables_pdfplumber, _extract_tables_pymupdf):
        try:
            tables = extractor(path)
        except Exception as exc:
            logger.warning("%s failed for %s: %s", extractor.__name__, path, exc)
            continue
        if tables:
            return tables
    return []


def _extract_tables_pdfplumber(pdf_path: Path) -> list[dict[str, Any]]:
    import pdfplumber

    items: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            width = float(page.width or 0)
            height = float(page.height or 0)
            for table in page.find_tables() or []:
                item = _table_item(
                    page_idx=page_index,
                    bbox=_normalize_bbox(getattr(table, "bbox", None), width, height),
                    rows=table.extract(),
                    source="pdfplumber",
                )
                if item is not None:
                    items.append(item)
    return items


def _extract_tables_pymupdf(pdf_path: Path) -> list[dict[str, Any]]:
    import fitz

    items: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf):
            width = float(page.rect.width)
            height = float(page.rect.height)
            found = page.find_tables()
            for table in getattr(found, "tables", []) or []:
                item = _table_item(
                    page_idx=page_index,
                    bbox=_normalize_bbox(getattr(table, "bbox", None), width, height),
                    rows=table.extract(),
                    source="pymupdf",
                )
                if item is not None:
                    items.append(item)
    return items


def _table_item(
    *,
    page_idx: int,
    bbox: list[float] | None,
    rows: Any,
    source: str,
) -> dict[str, Any] | None:
    cleaned = _clean_rows(rows)
    if sum(1 for row in cleaned for cell in row if cell) < 2:
        return None
    html = _rows_to_html(cleaned)
    if not html:
        return None
    return {
        "type": "table",
        "page_idx": page_idx,
        "bbox": bbox or [0.0, 0.0, 1000.0, 1000.0],
        "table_body": html,
        "table_caption": [],
        "table_footnote": [],
        "table_source": source,
    }


def _clean_rows(rows: Any) -> list[list[str]]:
    cleaned: list[list[str]] = []
    if not isinstance(rows, list):
        return cleaned
    for row in rows:
        if not isinstance(row, (list, tuple)):
            continue
        cells = ["" if cell is None else " ".join(str(cell).split()) for cell in row]
        if any(cells):
            cleaned.append(cells)
    return cleaned


def _rows_to_html(rows: list[list[str]]) -> str:
    body: list[str] = []
    for row in rows:
        cells = "".join(f"<td>{escape(cell)}</td>" for cell in row)
        body.append(f"<tr>{cells}</tr>")
    if not body:
        return ""
    return "<table>" + "".join(body) + "</table>"


def _normalize_bbox(bbox: Any, width: float, height: float) -> list[float] | None:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    if width <= 0 or height <= 0:
        return None
    x0, y0, x1, y1 = (float(value) for value in bbox)

    def scale(value: float, limit: float) -> float:
        return max(0.0, min(1000.0, value / limit * 1000.0))

    return [scale(x0, width), scale(y0, height), scale(x1, width), scale(y1, height)]
