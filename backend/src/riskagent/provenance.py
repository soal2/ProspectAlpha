"""Evidence location contract.

PDF evidence keeps the page and bounding box produced by a PDF parser.
Plain text keeps character offsets in the source string. A text chunk index
is not a PDF page, and a synthetic full-page box is not a coordinate.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field


LocatorKind = Literal["pdf_page", "text_offset"]
TEXT_OFFSET_PARSER_VERSION = "text-offset-v1"
TEXT_OFFSET_PARSER_VERSIONS = {TEXT_OFFSET_PARSER_VERSION, "local-text-parser-v1"}
# A4 points. Text ingestion used to stamp this box on every chunk.
MANUFACTURED_FULL_PAGE_BBOX = (0.0, 0.0, 595.0, 842.0)


class PdfRegion(BaseModel):
    page_number: int
    bbox: list[float] = Field(default_factory=list)
    bbox_coordinate_space: Literal["pdf_points", "normalized_1000"] | None = None


class TextChunk(BaseModel):
    text: str
    char_start: int
    char_end: int


def iter_text_chunks(text: str, *, size: int = 900) -> list[TextChunk]:
    """Split plain text on paragraph boundaries and record source offsets.

    ``char_start`` / ``char_end`` index ``text`` directly:
    ``text[char_start:char_end] == chunk.text``.
    """

    if size < 1:
        raise ValueError("text chunk size must be positive")
    spans = _paragraph_spans(text)
    chunks: list[TextChunk] = []
    for raw_start, raw_end in spans:
        raw = text[raw_start:raw_end]
        stripped = raw.strip()
        if not stripped:
            continue
        leading = len(raw) - len(raw.lstrip())
        content_start = raw_start + leading
        for offset in range(0, len(stripped), size):
            window = stripped[offset : offset + size]
            chunk = window.strip()
            if not chunk:
                continue
            inner_leading = len(window) - len(window.lstrip())
            char_start = content_start + offset + inner_leading
            char_end = char_start + len(chunk)
            chunks.append(TextChunk(text=chunk, char_start=char_start, char_end=char_end))
    if chunks:
        return chunks
    fallback = text.strip()
    if not fallback:
        return [TextChunk(text="未提供可解析文本。", char_start=0, char_end=0)]
    leading = len(text) - len(text.lstrip())
    return [TextChunk(text=fallback, char_start=leading, char_end=leading + len(fallback))]


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    pattern = re.compile(r"\n{2,}|\f")
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in pattern.finditer(text):
        spans.append((cursor, match.start()))
        cursor = match.end()
    spans.append((cursor, len(text)))
    return spans


def is_manufactured_full_page_bbox(bbox: list[float] | tuple[float, ...] | None) -> bool:
    if not bbox or len(bbox) != 4:
        return False
    try:
        values = tuple(float(item) for item in bbox)
    except (TypeError, ValueError):
        return False
    return values == MANUFACTURED_FULL_PAGE_BBOX


def is_real_pdf_bbox(bbox: list[float] | None) -> bool:
    if not bbox or len(bbox) != 4:
        return False
    try:
        x0, y0, x1, y1 = (float(item) for item in bbox)
    except (TypeError, ValueError):
        return False
    return x1 > x0 and y1 > y0


def passes_real_pdf_evidence(
    *,
    locator_kind: str,
    page_number: int | None,
    bbox: list[float] | None,
    source_hash: str,
    coordinates_are_pdf: bool,
    parser_version: str | None = None,
) -> bool:
    """True only for a parser-produced PDF page and a non-empty box.

    A real PDF parser may emit a full-page box. The text chunker must not:
    it has no PDF coordinates, so ``coordinates_are_pdf`` stays false and its
    parser version is ``text-offset-v1``. Either signal fails this check,
    including the historical ``[0, 0, 595, 842]`` stamp.
    """

    if locator_kind != "pdf_page" or not coordinates_are_pdf:
        return False
    if parser_version in TEXT_OFFSET_PARSER_VERSIONS:
        return False
    if page_number is None or page_number < 1 or not source_hash:
        return False
    if not is_real_pdf_bbox(bbox):
        return False
    if is_manufactured_full_page_bbox(bbox) and parser_version in TEXT_OFFSET_PARSER_VERSIONS:
        return False
    return True


def table_fields_from_metadata(metadata: dict[str, Any] | None) -> dict[str, str]:
    """Copy table header, row, column, and unit only when the source has them."""

    if not isinstance(metadata, dict):
        return {}
    aliases = {
        "table_header": ("table_header", "header"),
        "table_row": ("table_row", "row_label", "row"),
        "table_column": ("table_column", "column_label", "column", "column_index"),
        "table_unit": ("table_unit", "unit"),
    }
    fields: dict[str, str] = {}
    for target, keys in aliases.items():
        for key in keys:
            if key not in metadata:
                continue
            value = metadata.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            fields[target] = text
            break
    return fields


def pdf_regions_from_refs(
    refs: list[Any] | None,
    *,
    coordinate_space: str | None = None,
) -> list[PdfRegion]:
    """Keep each real page box. Missing boxes stay empty instead of being invented."""

    regions: list[PdfRegion] = []
    seen: set[tuple[int, tuple[float, ...]]] = set()
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        raw_page = ref.get("page_number", ref.get("page_idx"))
        try:
            page_number = int(raw_page)
        except (TypeError, ValueError):
            continue
        if page_number < 1:
            continue
        raw_bbox = ref.get("bbox")
        bbox = (
            [float(value) for value in raw_bbox]
            if isinstance(raw_bbox, list) and len(raw_bbox) == 4
            else []
        )
        key = (page_number, tuple(bbox))
        if key in seen:
            continue
        seen.add(key)
        space = ref.get("bbox_coordinate_space") or coordinate_space
        if space not in {"pdf_points", "normalized_1000"}:
            space = None
        regions.append(
            PdfRegion(
                page_number=page_number,
                bbox=bbox,
                bbox_coordinate_space=space,
            )
        )
    return regions


def chunk_location_fields(chunk: Any) -> dict[str, Any]:
    """Location fields safe to persist on a retrieval event or evidence row."""

    metadata = chunk.metadata if isinstance(getattr(chunk, "metadata", None), dict) else {}
    parser_version = str(metadata.get("parser_version") or "")
    locator_kind = str(metadata.get("locator_kind") or "")
    text_offset = locator_kind == "text_offset" or parser_version in TEXT_OFFSET_PARSER_VERSIONS
    coordinate_space = str(metadata.get("bbox_coordinate_space") or "")
    if coordinate_space not in {"pdf_points", "normalized_1000"}:
        coordinate_space = None if text_offset else "pdf_points"
    group_refs = metadata.get("table_group_bbox_refs")
    merged = list(getattr(chunk, "merged_bboxes", None) or [])
    refs = group_refs if isinstance(group_refs, list) and group_refs else merged
    regions = [] if text_offset else pdf_regions_from_refs(refs, coordinate_space=coordinate_space)
    page_idx = int(getattr(chunk, "page_idx", 0) or 0)
    region = next(
        (
            candidate
            for candidate in merged
            if isinstance(candidate, dict) and int(candidate.get("page_idx", -1)) == page_idx
        ),
        {},
    )
    bbox = [] if text_offset else [float(value) for value in region.get("bbox", [])]
    page_number = None if text_offset or page_idx < 1 else page_idx
    if not text_offset and regions:
        page_number = regions[0].page_number
        if not bbox:
            bbox = list(regions[0].bbox)
    fields: dict[str, Any] = {
        "page_number": page_number,
        "bbox": bbox,
        "bbox_coordinate_space": None if text_offset else coordinate_space,
        "locator_kind": "text_offset" if text_offset else "pdf_page",
        "coordinates_are_pdf": not text_offset and bool(metadata.get("coordinates_are_pdf", True)),
        "parser_version": parser_version,
        "char_start": int(metadata.get("char_start") or 0),
        "char_end": int(metadata.get("char_end") or 0),
        "pdf_regions": [region.model_dump() for region in regions],
    }
    if not text_offset:
        fields.update(table_fields_from_metadata(metadata))
    return fields


def format_evidence_location(item: Any) -> str:
    """Render a location without turning a missing PDF page into a page number."""

    locator_kind = getattr(item, "locator_kind", None)
    if locator_kind is None and isinstance(item, dict):
        locator_kind = item.get("locator_kind")
    page_number = _read(item, "page_number")
    char_start = _read(item, "char_start") or 0
    char_end = _read(item, "char_end") or 0
    regions = _read(item, "pdf_regions") or []
    pages: list[int] = []
    for region in regions:
        raw_page = _read(region, "page_number")
        try:
            number = int(raw_page)
        except (TypeError, ValueError):
            continue
        if number >= 1 and number not in pages:
            pages.append(number)
    if locator_kind != "text_offset" and isinstance(page_number, int) and page_number >= 1:
        if page_number not in pages:
            pages.insert(0, page_number)
    parts: list[str] = []
    if locator_kind == "text_offset" or not pages:
        parts.append("无 PDF 页")
        if char_end or char_start:
            parts.append(f"文本偏移 {int(char_start)}–{int(char_end)}")
    else:
        parts.append("、".join(f"P.{page}" for page in pages))
    for label, key in (
        ("表头", "table_header"),
        ("行", "table_row"),
        ("列", "table_column"),
        ("单位", "table_unit"),
    ):
        value = _read(item, key)
        if isinstance(value, str) and value.strip():
            parts.append(f"{label} {value.strip()}")
    section = _read(item, "section_path")
    if isinstance(section, str) and section.strip():
        parts.append(section.strip())
    return " · ".join(parts)


def _read(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)
