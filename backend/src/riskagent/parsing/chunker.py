"""Chunker factory and block-based MinerU content-list chunker."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol

from riskagent.database import BACKEND_ROOT


DEFAULT_CHUNKER_NAME = "block"
DEFAULT_MAX_CHARS = 1200
DEFAULT_INCLUDE_TYPES = ("text", "table", "image")
DEFAULT_SKIP_TYPES = ("header", "footer", "page_number")
DEFAULT_IMAGE_CONTEXT_WINDOW_BLOCKS = 3

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BBoxRef:
    page_idx: int
    bbox: list[float]
    source_item_index: int


@dataclass(frozen=True)
class MergedBBox:
    page_idx: int
    bbox: list[float]


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    document_id: str
    doc_key: str
    chunk_type: str
    text: str
    page_idx: int
    source_file: str
    source_item_indices: list[int]
    bbox_refs: list[BBoxRef]
    merged_bboxes: list[MergedBBox]
    image_path: str | None = None
    mineru_img_path: str | None = None
    table_html: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    content_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChunkRequest:
    doc_key: str
    content_list_path: Path | str
    document_id: str | None = None
    output_dir: Path | str | None = None
    max_chars: int = DEFAULT_MAX_CHARS
    include_types: tuple[str, ...] = DEFAULT_INCLUDE_TYPES
    skip_types: tuple[str, ...] = DEFAULT_SKIP_TYPES
    merge_text: bool = True
    require_image_description: bool = False
    image_context_window_blocks: int = DEFAULT_IMAGE_CONTEXT_WINDOW_BLOCKS


@dataclass(frozen=True)
class ChunkResult:
    document_id: str
    doc_key: str
    chunker_name: str
    chunker_version: str
    content_list_path: Path
    output_path: Path | None
    chunks: list[DocumentChunk]


class ImageDescriber(Protocol):
    def describe(
        self,
        image_path: Path,
        source_item: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> str:
        ...


class DocumentChunker(Protocol):
    name: str
    version: str

    def chunk(self, request: ChunkRequest) -> ChunkResult:
        ...


class _ImageDescriptionCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data = self._load()

    @classmethod
    def for_output_dir(cls, output_dir: Path | str | None) -> "_ImageDescriptionCache | None":
        if output_dir is None:
            return None
        path = Path(output_dir).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return cls(path / "image_descriptions.json")

    def get(self, key: str) -> str:
        value = self._data.get(key)
        return value if isinstance(value, str) else ""

    def set(self, key: str, description: str) -> None:
        self._data[key] = description
        self._write()

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): str(value) for key, value in payload.items() if isinstance(value, str)}

    def _write(self) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)


class BlockChunker:
    name = DEFAULT_CHUNKER_NAME
    version = "block-v1"

    def __init__(self, *, image_describer: ImageDescriber | None = None) -> None:
        self.image_describer = image_describer

    def chunk(self, request: ChunkRequest) -> ChunkResult:
        content_list_path = Path(request.content_list_path).expanduser().resolve()
        content = json.loads(content_list_path.read_text(encoding="utf-8"))
        if not isinstance(content, list):
            raise ValueError(f"MinerU content list must be a JSON array: {content_list_path}")

        document_id = request.document_id or request.doc_key
        image_description_cache = _ImageDescriptionCache.for_output_dir(request.output_dir)
        chunks: list[DocumentChunk] = []
        text_buffer: list[tuple[int, dict[str, Any]]] = []
        last_table_chunk_indices: list[int] = []
        skip_source_item_indices: set[int] = set()

        def flush_text() -> None:
            nonlocal text_buffer
            if not text_buffer:
                return
            chunks.append(
                self._build_text_chunk(
                    doc_key=request.doc_key,
                    document_id=document_id,
                    chunk_number=len(chunks) + 1,
                    items=text_buffer,
                    source_file=_project_relative(content_list_path),
                )
            )
            text_buffer = []

        for source_item_index, item in enumerate(content):
            if source_item_index in skip_source_item_indices:
                continue
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", ""))
            if item_type in request.skip_types:
                flush_text()
                continue
            if item_type not in request.include_types:
                flush_text()
                last_table_chunk_indices = []
                continue

            if item_type == "text":
                last_table_chunk_indices = []
                text = _text_value(item.get("text"))
                if not text:
                    continue
                if len(text) > request.max_chars:
                    flush_text()
                    for part in _split_plain_text(text, request.max_chars):
                        split_item = dict(item)
                        split_item["text"] = part
                        chunks.append(
                            self._build_text_chunk(
                                doc_key=request.doc_key,
                                document_id=document_id,
                                chunk_number=len(chunks) + 1,
                                items=[(source_item_index, split_item)],
                                source_file=_project_relative(content_list_path),
                            )
                        )
                    continue
                if not request.merge_text:
                    flush_text()
                    text_buffer = [(source_item_index, item)]
                    flush_text()
                    continue
                next_text = "\n".join(_text_value(entry.get("text")) for _, entry in text_buffer + [(source_item_index, item)])
                if text_buffer and len(next_text) > request.max_chars:
                    flush_text()
                text_buffer.append((source_item_index, item))
                continue

            flush_text()
            if item_type == "table":
                continuation_items = _collect_table_continuations(content, source_item_index)
                table_chunks = self._build_table_chunks(
                    doc_key=request.doc_key,
                    document_id=document_id,
                    start_chunk_number=len(chunks) + 1,
                    source_item_index=source_item_index,
                    item=item,
                    content_list_path=content_list_path,
                    max_chars=request.max_chars,
                    content=content,
                    continuation_items=continuation_items,
                )
                if table_chunks:
                    start_index = len(chunks)
                    chunks.extend(table_chunks)
                    last_table_chunk_indices = list(range(start_index, len(chunks)))
                    skip_source_item_indices.update(index for index, _ in continuation_items)
                else:
                    chunks = _attach_table_continuation(
                        chunks=chunks,
                        table_chunk_indices=last_table_chunk_indices,
                        source_item_index=source_item_index,
                        item=item,
                        content=content,
                    )
                continue
            elif item_type == "image":
                last_table_chunk_indices = []
                chunk = self._build_image_chunk(
                    doc_key=request.doc_key,
                    document_id=document_id,
                    chunk_number=len(chunks) + 1,
                    source_item_index=source_item_index,
                    item=item,
                    content_list_path=content_list_path,
                    require_description=request.require_image_description,
                    context=_build_image_context(
                        content=content,
                        source_item_index=source_item_index,
                        window_blocks=request.image_context_window_blocks,
                    ),
                    description_cache=image_description_cache,
                )
            else:
                last_table_chunk_indices = []
                continue
            if chunk is not None:
                chunks.append(chunk)

        flush_text()
        for output_chunk in chunks:
            output_chunk.metadata.setdefault("bbox_coordinate_space", "normalized_1000")
            output_chunk.metadata.setdefault("page_number_base", 1)
        chunks = [
            replace(chunk, content_sha256=_compute_content_sha256(chunk.text))
            for chunk in chunks
        ]
        output_path = self._write_chunks(request.output_dir, chunks)
        return ChunkResult(
            document_id=document_id,
            doc_key=request.doc_key,
            chunker_name=self.name,
            chunker_version=self.version,
            content_list_path=content_list_path,
            output_path=output_path,
            chunks=chunks,
        )

    def _build_text_chunk(
        self,
        *,
        doc_key: str,
        document_id: str,
        chunk_number: int,
        items: list[tuple[int, dict[str, Any]]],
        source_file: str,
    ) -> DocumentChunk:
        texts = [_text_value(item.get("text")) for _, item in items]
        bbox_refs = [_bbox_ref(index, item) for index, item in items if _has_bbox(item)]
        return DocumentChunk(
            chunk_id=_chunk_id(document_id, chunk_number),
            document_id=document_id,
            doc_key=doc_key,
            chunk_type="text",
            text="\n".join(text for text in texts if text),
            page_idx=_page_idx(items[0][1]),
            source_file=source_file,
            source_item_indices=[index for index, _ in items],
            bbox_refs=bbox_refs,
            merged_bboxes=_merge_bboxes(bbox_refs),
        )

    def _build_table_chunks(
        self,
        *,
        doc_key: str,
        document_id: str,
        start_chunk_number: int,
        source_item_index: int,
        item: dict[str, Any],
        content_list_path: Path,
        max_chars: int,
        content: list[Any],
        continuation_items: list[tuple[int, dict[str, Any]]],
    ) -> list[DocumentChunk]:
        table_html = _text_value(item.get("table_body"))
        caption = _join_text_list(item.get("table_caption"))
        footnote = _join_text_list(item.get("table_footnote"))
        text = "\n".join(part for part in (caption, _html_to_text(table_html), footnote) if part)
        if not text.strip():
            return []

        initial_bbox_ref, bbox_source = _bbox_ref_or_inferred(source_item_index, item, content)
        group_refs = [initial_bbox_ref] if initial_bbox_ref is not None else []
        group_bbox_sources = {source_item_index: bbox_source}
        for continuation_index, continuation_item in continuation_items:
            continuation_ref, continuation_bbox_source = _bbox_ref_or_inferred(
                continuation_index,
                continuation_item,
                content,
            )
            group_bbox_sources[continuation_index] = continuation_bbox_source
            if continuation_ref is not None:
                group_refs.append(continuation_ref)

        text_parts = _split_table_text(
            table_html=table_html,
            caption=caption,
            footnote=footnote,
            max_chars=max_chars,
            min_parts=max(1, len(group_refs)),
        )
        table_group_bbox_refs = [_bbox_ref_to_dict(ref) for ref in group_refs]
        table_group_source_item_indices = [source_item_index, *[index for index, _ in continuation_items]]
        chunks: list[DocumentChunk] = []
        for offset, part in enumerate(text_parts):
            assigned_ref = _assigned_table_bbox_ref(
                group_refs=group_refs,
                table_part_index=offset + 1,
                table_part_count=len(text_parts),
            )
            bbox_refs = [assigned_ref] if assigned_ref is not None else []
            local_source_item_indices = [source_item_index]
            if assigned_ref is not None and assigned_ref.source_item_index != source_item_index:
                local_source_item_indices.append(assigned_ref.source_item_index)
            assigned_bbox_source = (
                group_bbox_sources.get(assigned_ref.source_item_index, "missing_bbox")
                if assigned_ref is not None
                else "missing_bbox"
            )
            chunks.append(
                DocumentChunk(
                    chunk_id=_chunk_id(document_id, start_chunk_number + offset),
                    document_id=document_id,
                    doc_key=doc_key,
                    chunk_type="table",
                    text=part,
                    page_idx=assigned_ref.page_idx if assigned_ref is not None else _page_idx(item),
                    source_file=_project_relative(content_list_path),
                    source_item_indices=local_source_item_indices,
                    bbox_refs=bbox_refs,
                    merged_bboxes=_merge_bboxes(bbox_refs),
                    image_path=_resolve_mineru_image_path(content_list_path, item.get("img_path")),
                    mineru_img_path=_text_value(item.get("img_path")) or None,
                    table_html=table_html if len(text_parts) == 1 or offset == 0 else None,
                    metadata={
                        "table_caption": item.get("table_caption") or [],
                        "table_part_index": offset + 1,
                        "table_part_count": len(text_parts),
                        "table_split": len(text_parts) > 1,
                        "table_header": _table_header_from_html(table_html),
                        "bbox_source": assigned_bbox_source,
                        "table_group_source_item_indices": table_group_source_item_indices,
                        "table_group_bbox_refs": table_group_bbox_refs,
                        "continuation_source_item_indices": [index for index, _ in continuation_items],
                    },
                )
            )
        return chunks

    def _build_image_chunk(
        self,
        *,
        doc_key: str,
        document_id: str,
        chunk_number: int,
        source_item_index: int,
        item: dict[str, Any],
        content_list_path: Path,
        require_description: bool,
        context: dict[str, Any],
        description_cache: "_ImageDescriptionCache | None",
    ) -> DocumentChunk | None:
        image_path = _resolve_mineru_image_path(content_list_path, item.get("img_path"))
        description = ""
        description_source = "mineru_caption"
        if self.image_describer is not None and image_path:
            cache_key = _image_description_cache_key(source_item_index, item)
            description = description_cache.get(cache_key) if description_cache is not None else ""
            if description:
                description_source = "vlm"
            if not description:
                try:
                    description = self.image_describer.describe(
                        _resolve_mineru_image_absolute_path(content_list_path, item.get("img_path")),
                        item,
                        context,
                    ).strip()
                except Exception:
                    logger.warning(
                        "Image description failed; falling back to MinerU caption for document %s item %s.",
                        document_id,
                        source_item_index,
                        exc_info=True,
                    )
                if description:
                    description_source = "vlm"
                if description and description_cache is not None:
                    description_cache.set(cache_key, description)
        if not description:
            description = "\n".join(
                part
                for part in (
                    _join_text_list(item.get("image_caption")),
                    _join_text_list(item.get("image_footnote")),
                )
                if part
            )
        if require_description and not description:
            return None

        bbox_refs = [_bbox_ref(source_item_index, item)] if _has_bbox(item) else []
        return DocumentChunk(
            chunk_id=_chunk_id(document_id, chunk_number),
            document_id=document_id,
            doc_key=doc_key,
            chunk_type="image",
            text=description,
            page_idx=_page_idx(item),
            source_file=_project_relative(content_list_path),
            source_item_indices=[source_item_index],
            bbox_refs=bbox_refs,
            merged_bboxes=_merge_bboxes(bbox_refs),
            image_path=image_path,
            mineru_img_path=_text_value(item.get("img_path")) or None,
            metadata={
                "description_source": description_source,
                "image_caption": item.get("image_caption") or [],
                "image_footnote": item.get("image_footnote") or [],
                "image_context": context,
            },
        )

    def _write_chunks(
        self, output_dir: Path | str | None, chunks: list[DocumentChunk]
    ) -> Path | None:
        if output_dir is None:
            return None
        path = Path(output_dir).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        output_path = path / "chunks.jsonl"
        with output_path.open("w", encoding="utf-8") as fh:
            for chunk in chunks:
                fh.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
        return output_path


class ChunkerFactory:
    def __init__(self, *, image_describer: ImageDescriber | None = None) -> None:
        self.image_describer = image_describer
        self._chunkers: dict[str, type[BlockChunker]] = {
            BlockChunker.name: BlockChunker,
        }

    def register(self, name: str, chunker: type[BlockChunker]) -> None:
        self._chunkers[name] = chunker

    def create(self, name: str = DEFAULT_CHUNKER_NAME) -> DocumentChunker:
        try:
            chunker = self._chunkers[name]
        except KeyError as exc:
            known = ", ".join(sorted(self._chunkers))
            raise ValueError(f"Unknown chunker '{name}'. Known chunkers: {known}") from exc
        return chunker(image_describer=self.image_describer)


class _TableTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


class _TableRowTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[str] = []
        self.row_cells: list[list[str]] = []
        self.row_has_th: list[bool] = []
        self._in_row = False
        self._in_cell = False
        self._current_cell_is_header = False
        self._current_row_has_th = False
        self._cell_parts: list[str] = []
        self._row_cells: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._in_row = True
            self._row_cells = []
            self._current_row_has_th = False
        elif tag in {"td", "th"} and self._in_row:
            self._in_cell = True
            self._current_cell_is_header = tag == "th"
            self._current_row_has_th = self._current_row_has_th or tag == "th"
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text and self._in_row and self._in_cell:
            self._cell_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell:
            cell = " ".join(self._cell_parts).strip()
            if cell:
                self._row_cells.append(cell)
            self._cell_parts = []
            self._in_cell = False
            self._current_cell_is_header = False
        elif tag == "tr" and self._in_row:
            row = " | ".join(self._row_cells).strip()
            if row:
                self.rows.append(row)
                self.row_cells.append(list(self._row_cells))
                self.row_has_th.append(self._current_row_has_th)
            self._row_cells = []
            self._in_row = False


def _chunk_id(document_id: str, chunk_number: int) -> str:
    return f"{document_id}_chunk_{chunk_number:06d}"


def _compute_content_sha256(text: str) -> str:
    """Compute SHA-256 hex digest of chunk text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _image_description_cache_key(source_item_index: int, item: dict[str, Any]) -> str:
    bbox = item.get("bbox")
    return json.dumps(
        {
            "source_item_index": source_item_index,
            "page_idx": item.get("page_idx", ""),
            "img_path": _text_value(item.get("img_path")),
            "bbox": bbox if isinstance(bbox, list) else None,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _page_idx(item: dict[str, Any]) -> int:
    # MinerU source items are zero-based, while PostgreSQL chunks and every
    # public evidence API use the PDF/PDF.js one-based page number.
    return int(item.get("page_idx", 0)) + 1


def _text_value(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _join_text_list(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item.strip())
        elif isinstance(item, dict):
            parts.append(_text_value(item.get("text") or item.get("content")).strip())
    return "\n".join(part for part in parts if part)


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    parser = _TableTextExtractor()
    parser.feed(value)
    return " ".join(parser.parts)


def _html_table_rows(value: str) -> list[str]:
    if not value:
        return []
    parser = _TableRowTextExtractor()
    parser.feed(value)
    return parser.rows


def _html_table_row_data(value: str) -> tuple[list[str], str]:
    if not value:
        return [], ""
    parser = _TableRowTextExtractor()
    parser.feed(value)
    header = _infer_table_header(parser.rows, parser.row_cells, parser.row_has_th)
    return parser.rows, header


def _table_header_from_html(value: str) -> str:
    _, header = _html_table_row_data(value)
    return header


def _split_table_text(
    *,
    table_html: str,
    caption: str,
    footnote: str,
    max_chars: int,
    min_parts: int = 1,
) -> list[str]:
    full_text = "\n".join(part for part in (caption, _html_to_text(table_html), footnote) if part)
    limit = max(1, max_chars)
    if len(full_text) <= limit:
        return _expand_parts_to_min_count([full_text], max_chars=limit, min_parts=min_parts)

    rows, header = _html_table_row_data(table_html)
    if not rows:
        return _split_plain_text(full_text, limit)

    row_limit = max(1, limit - len(header) - 1) if header else limit
    parts: list[str] = []
    buffer: list[str] = []
    for row in rows:
        candidate = "\n".join(buffer + [row])
        if buffer and len(candidate) > row_limit:
            parts.append("\n".join(buffer))
            buffer = []
        if len(row) > row_limit:
            if buffer:
                parts.append("\n".join(buffer))
                buffer = []
            parts.extend(_split_plain_text(row, row_limit))
        else:
            buffer.append(row)
    if buffer:
        parts.append("\n".join(buffer))

    if caption and parts:
        parts[0] = caption + "\n" + parts[0]
    if header:
        parts = [_prepend_table_header(part, header, limit) for part in parts]
    if footnote and parts:
        parts[-1] = parts[-1] + "\n" + footnote
    return _expand_parts_to_min_count(
        [part for part in parts if part.strip()],
        max_chars=limit,
        min_parts=min_parts,
    )


def _infer_table_header(
    rows: list[str],
    row_cells: list[list[str]],
    row_has_th: list[bool],
) -> str:
    if not rows or not row_cells:
        return ""
    if row_has_th and row_has_th[0]:
        return rows[0]
    first_cells = row_cells[0]
    if len(first_cells) < 2:
        return ""
    first_row = rows[0]
    if len(first_row) > 150:
        return ""
    if "指" in first_cells[:3]:
        return ""
    non_empty = [cell for cell in first_cells if cell.strip()]
    if len(non_empty) < 2:
        return ""
    average_len = sum(len(cell) for cell in non_empty) / len(non_empty)
    if average_len > 24:
        return ""
    return first_row


def _prepend_table_header(part: str, header: str, max_chars: int) -> str:
    if not part.strip() or part.startswith(header):
        return part
    return header + "\n" + part


def _split_plain_text(text: str, max_chars: int) -> list[str]:
    clean = text.strip()
    if not clean:
        return []
    return [clean[index : index + max_chars] for index in range(0, len(clean), max_chars)]


def _expand_parts_to_min_count(
    parts: list[str],
    *,
    max_chars: int,
    min_parts: int,
) -> list[str]:
    expanded = [part for part in parts if part.strip()]
    while len(expanded) < min_parts:
        largest_index, largest = max(enumerate(expanded), key=lambda entry: len(entry[1]))
        if len(largest) <= 1:
            break
        split_at = _split_point(largest)
        left = largest[:split_at].strip()
        right = largest[split_at:].strip()
        if not left or not right:
            break
        expanded[largest_index : largest_index + 1] = [left, right]
    final: list[str] = []
    for part in expanded:
        if len(part) > max_chars:
            final.extend(_split_plain_text(part, max_chars))
        else:
            final.append(part)
    return final


def _split_point(text: str) -> int:
    midpoint = max(1, len(text) // 2)
    newline_before = text.rfind("\n", 0, midpoint)
    newline_after = text.find("\n", midpoint)
    candidates = [value for value in (newline_before, newline_after) if value > 0]
    if candidates:
        return min(candidates, key=lambda value: abs(value - midpoint))
    return midpoint


def _collect_table_continuations(
    content: list[Any],
    source_item_index: int,
) -> list[tuple[int, dict[str, Any]]]:
    continuations: list[tuple[int, dict[str, Any]]] = []
    for index in range(source_item_index + 1, len(content)):
        candidate = content[index]
        if not isinstance(candidate, dict):
            break
        item_type = str(candidate.get("type", ""))
        if item_type in DEFAULT_SKIP_TYPES:
            continue
        if item_type != "table":
            break
        if _table_item_text(candidate):
            break
        continuations.append((index, candidate))
    return continuations


def _table_item_text(item: dict[str, Any]) -> str:
    table_html = _text_value(item.get("table_body"))
    caption = _join_text_list(item.get("table_caption"))
    footnote = _join_text_list(item.get("table_footnote"))
    return "\n".join(part for part in (caption, _html_to_text(table_html), footnote) if part)


def _attach_table_continuation(
    *,
    chunks: list[DocumentChunk],
    table_chunk_indices: list[int],
    source_item_index: int,
    item: dict[str, Any],
    content: list[Any],
) -> list[DocumentChunk]:
    if not table_chunk_indices:
        return chunks

    continuation_ref, continuation_bbox_source = _bbox_ref_or_inferred(
        source_item_index,
        item,
        content,
    )
    updated = list(chunks)
    first_chunk = updated[table_chunk_indices[0]]
    first_metadata = dict(first_chunk.metadata or {})
    group_refs = [
        _bbox_ref_from_dict(ref)
        for ref in first_metadata.get("table_group_bbox_refs", [])
        if isinstance(ref, dict)
    ]
    if not group_refs:
        for chunk_index in table_chunk_indices:
            group_refs.extend(updated[chunk_index].bbox_refs)
    if continuation_ref is not None:
        group_refs.append(continuation_ref)
    group_source_indices = list(first_metadata.get("table_group_source_item_indices") or [])
    if source_item_index not in group_source_indices:
        group_source_indices.append(source_item_index)

    for chunk_index in table_chunk_indices:
        chunk = updated[chunk_index]
        metadata = dict(chunk.metadata or {})
        continuation_indices = list(metadata.get("continuation_source_item_indices") or [])
        if source_item_index not in continuation_indices:
            continuation_indices.append(source_item_index)
        assigned_ref = _assigned_table_bbox_ref(
            group_refs=group_refs,
            table_part_index=int(metadata.get("table_part_index", 1)),
            table_part_count=int(metadata.get("table_part_count", len(table_chunk_indices))),
        )
        bbox_refs = [assigned_ref] if assigned_ref is not None else []
        local_source_indices = [group_source_indices[0]] if group_source_indices else list(chunk.source_item_indices)
        if assigned_ref is not None and assigned_ref.source_item_index not in local_source_indices:
            local_source_indices.append(assigned_ref.source_item_index)
        metadata["continuation_source_item_indices"] = continuation_indices
        metadata["table_group_source_item_indices"] = group_source_indices
        metadata["table_group_bbox_refs"] = [_bbox_ref_to_dict(ref) for ref in group_refs]
        metadata["bbox_source"] = (
            continuation_bbox_source
            if assigned_ref is not None and assigned_ref.source_item_index == source_item_index
            else metadata.get("bbox_source", "mineru_bbox")
        )
        updated[chunk_index] = DocumentChunk(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            doc_key=chunk.doc_key,
            chunk_type=chunk.chunk_type,
            text=chunk.text,
            page_idx=assigned_ref.page_idx if assigned_ref is not None else chunk.page_idx,
            source_file=chunk.source_file,
            source_item_indices=local_source_indices,
            bbox_refs=bbox_refs,
            merged_bboxes=_merge_bboxes(bbox_refs),
            image_path=chunk.image_path,
            mineru_img_path=chunk.mineru_img_path,
            table_html=chunk.table_html,
            metadata=metadata,
        )
    return updated


def _has_bbox(item: dict[str, Any]) -> bool:
    bbox = item.get("bbox")
    return isinstance(bbox, list) and len(bbox) == 4


def _bbox_ref_or_inferred(
    source_item_index: int,
    item: dict[str, Any],
    content: list[Any],
) -> tuple[BBoxRef | None, str]:
    if _has_bbox(item):
        return _bbox_ref(source_item_index, item), "mineru_bbox"
    page_idx = _page_idx(item)
    same_page_boxes = [
        [float(value) for value in candidate["bbox"]]
        for candidate in content
        if isinstance(candidate, dict)
        and _page_idx(candidate) == page_idx
        and _has_bbox(candidate)
    ]
    if same_page_boxes:
        return (
            BBoxRef(
                page_idx=page_idx,
                bbox=[
                    min(box[0] for box in same_page_boxes),
                    min(box[1] for box in same_page_boxes),
                    max(box[2] for box in same_page_boxes),
                    max(box[3] for box in same_page_boxes),
                ],
                source_item_index=source_item_index,
            ),
            "inferred_content_area",
        )
    return None, "missing_bbox"


def _bbox_ref(source_item_index: int, item: dict[str, Any]) -> BBoxRef:
    return BBoxRef(
        page_idx=_page_idx(item),
        bbox=[float(value) for value in item["bbox"]],
        source_item_index=source_item_index,
    )


def _bbox_ref_to_dict(ref: BBoxRef) -> dict[str, Any]:
    return {
        "page_idx": ref.page_idx,
        "bbox": list(ref.bbox),
        "source_item_index": ref.source_item_index,
    }


def _bbox_ref_from_dict(payload: dict[str, Any]) -> BBoxRef:
    return BBoxRef(
        page_idx=int(payload["page_idx"]),
        bbox=[float(value) for value in payload["bbox"]],
        source_item_index=int(payload["source_item_index"]),
    )


def _assigned_table_bbox_ref(
    *,
    group_refs: list[BBoxRef],
    table_part_index: int,
    table_part_count: int,
) -> BBoxRef | None:
    if not group_refs:
        return None
    if table_part_count <= 1:
        return group_refs[0]
    zero_based_part = max(0, table_part_index - 1)
    ref_index = min(
        len(group_refs) - 1,
        int(zero_based_part * len(group_refs) / max(1, table_part_count)),
    )
    return group_refs[ref_index]


def _merge_bboxes(bbox_refs: list[BBoxRef]) -> list[MergedBBox]:
    by_page: dict[int, list[list[float]]] = {}
    for ref in bbox_refs:
        by_page.setdefault(ref.page_idx, []).append(ref.bbox)

    merged: list[MergedBBox] = []
    for page_idx, boxes in sorted(by_page.items()):
        merged.append(
            MergedBBox(
                page_idx=page_idx,
                bbox=[
                    min(box[0] for box in boxes),
                    min(box[1] for box in boxes),
                    max(box[2] for box in boxes),
                    max(box[3] for box in boxes),
                ],
            )
        )
    return merged


def _build_image_context(
    *,
    content: list[Any],
    source_item_index: int,
    window_blocks: int,
) -> dict[str, Any]:
    item = content[source_item_index]
    if not isinstance(item, dict):
        return {}
    page_idx = _page_idx(item)
    window = max(0, window_blocks)
    before: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []

    for index in range(source_item_index - 1, -1, -1):
        candidate = content[index]
        if _is_context_text(candidate, page_idx):
            before.append(_context_text_item(index, candidate))
            if len(before) >= window:
                break
    before.reverse()

    for index in range(source_item_index + 1, len(content)):
        candidate = content[index]
        if _is_context_text(candidate, page_idx):
            after.append(_context_text_item(index, candidate))
            if len(after) >= window:
                break

    return {
        "page_idx": page_idx,
        "image_caption": item.get("image_caption") or [],
        "image_footnote": item.get("image_footnote") or [],
        "nearby_text_before": before,
        "nearby_text_after": after,
    }


def _is_context_text(candidate: Any, page_idx: int) -> bool:
    return (
        isinstance(candidate, dict)
        and candidate.get("type") == "text"
        and _page_idx(candidate) == page_idx
        and bool(_text_value(candidate.get("text")).strip())
    )


def _context_text_item(source_item_index: int, item: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_item_index": source_item_index,
        "text": _text_value(item.get("text")).strip(),
    }
    if _has_bbox(item):
        payload["bbox"] = [float(value) for value in item["bbox"]]
    return payload


def _resolve_mineru_image_path(content_list_path: Path, mineru_img_path: Any) -> str | None:
    if not isinstance(mineru_img_path, str) or not mineru_img_path:
        return None
    image_path = (content_list_path.parent / mineru_img_path).resolve()
    return _project_relative(image_path)


def _resolve_mineru_image_absolute_path(content_list_path: Path, mineru_img_path: Any) -> Path:
    if not isinstance(mineru_img_path, str) or not mineru_img_path:
        raise ValueError("MinerU image item is missing img_path.")
    return (content_list_path.parent / mineru_img_path).resolve()


def _project_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(BACKEND_ROOT.parent))
    except ValueError:
        return str(path.resolve())
