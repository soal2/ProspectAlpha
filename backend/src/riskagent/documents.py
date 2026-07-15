"""Document asset storage and PDF parsing for the analysis pipeline."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid
from urllib.parse import unquote, urlparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any, Literal

import fitz
import yaml
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from riskagent.database import Base, SessionLocal, init_db
from riskagent.ingestion import (
    ProspectusIngestionRepository,
    ProspectusRegisterRequest,
    ProspectusRegistration,
)
from riskagent.models import DocumentAssetRow, DocumentBlockRow, ProspectusDocumentRow
from riskagent.parsing import (
    ChunkResult,
    ChunkerFactory,
    DocumentChunk,
    ImageDescriber,
    PdfProcessResult,
    PdfProcessorFactory,
    build_chunk_request,
    build_pdf_process_request,
    load_rag_pipeline_settings,
)
from riskagent.parsing.chunker import BBoxRef, MergedBBox
from riskagent.providers.object_store import ObjectStore, get_default_object_store
from riskagent.providers.vllm import VLLMImageDescriber
from riskagent.retrieval import ChunkIndexer, PostgresChunkIndexer, create_chunk_indexer

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = BACKEND_ROOT / "data"
DEFAULT_DOCUMENTS_PATH = DATA_ROOT / "processed" / "documents.yaml"
DEFAULT_DOCUMENT_STORAGE_DIR = DATA_ROOT / "raw" / "documents"
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
PARSER_VERSION = "pymupdf-v1"
logger = logging.getLogger(__name__)
_ACTIVE_PROSPECTUS_PIPELINES: set[str] = set()
_ACTIVE_PROSPECTUS_PIPELINES_LOCK = threading.Lock()


@dataclass(frozen=True)
class ProspectusPipelineRequest:
    source_path: Path | str | None = None
    content: bytes | None = None
    original_filename: str | None = None
    doc_key: str | None = None
    source_url: str | None = None
    config_path: Path | str | None = None
    processed_root: Path | str = DATA_ROOT / "processed"
    chunk_output_dir: Path | str | None = None
    enable_image_vlm: bool = True
    index_chunks: bool = True
    index_backend: str = "postgres"
    index_schema: str = "rag"


@dataclass(frozen=True)
class ProspectusPipelineResult:
    registration: ProspectusRegistration
    parsed: PdfProcessResult
    chunked: ChunkResult
    indexed_chunk_count: int | None

    @property
    def chunks_path(self) -> Path | None:
        return self.chunked.output_path


class DocumentCreate(BaseModel):
    filename: str = "prospectus.txt"
    content_text: str
    source_url: str | None = None


class DocumentBlock(BaseModel):
    block_id: str
    document_id: str
    page_number: int
    section_path: str
    block_type: str = "text"
    text: str
    bbox: list[float]
    bbox_coordinate_space: Literal["pdf_points", "normalized_1000"] = "pdf_points"
    char_start: int
    char_end: int
    source_hash: str
    parser_version: str = PARSER_VERSION


class DocumentAsset(BaseModel):
    document_id: str
    filename: str
    content_type: str
    file_sha256: str
    storage_uri: str
    file_size_bytes: int
    page_count: int
    text_chars: int
    block_count: int
    processing_status: Literal["uploaded", "queued", "processing", "ready", "failed"] = "ready"
    source_url: str | None = None
    created_at: str
    blocks: list[DocumentBlock] = Field(default_factory=list, exclude=True)


class DocumentRepository:
    def __init__(
        self,
        path: Path | str | None = None,
        storage_dir: Path | str | None = None,
        session_factory=SessionLocal,
        use_database: bool | None = None,
        object_store: ObjectStore | None = None,
        chunk_indexer: PostgresChunkIndexer | None = None,
    ) -> None:
        self.path = Path(path or os.getenv("DOCUMENTS_PATH") or DEFAULT_DOCUMENTS_PATH)
        self.storage_dir = Path(
            storage_dir or os.getenv("DOCUMENT_STORAGE_DIR") or DEFAULT_DOCUMENT_STORAGE_DIR
        )
        self.session_factory = session_factory
        self.use_database = use_database if use_database is not None else os.getenv("USE_DATABASE", "1") != "0"
        self.object_store = object_store or get_default_object_store()
        self.chunk_indexer = chunk_indexer
        if self.use_database:
            bind = getattr(session_factory, "kw", {}).get("bind")
            if bind is None:
                init_db()
            else:
                Base.metadata.create_all(bind=bind)

    def create(self, payload: DocumentCreate) -> DocumentAsset:
        raw = payload.content_text.encode("utf-8")
        return self.create_from_bytes(
            filename=payload.filename,
            content=raw,
            content_type="text/plain",
            source_url=payload.source_url,
        )

    def create_from_bytes(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
        source_url: str | None = None,
    ) -> DocumentAsset:
        if len(content) > MAX_DOCUMENT_BYTES:
            raise ValueError("Document exceeds 50MB upload limit.")
        is_pdf = content_type == "application/pdf" or filename.lower().endswith(".pdf")
        if not is_pdf and not content_type.startswith("text/"):
            raise ValueError("Only PDF and text prospectus assets are supported.")

        file_sha256 = hashlib.sha256(content).hexdigest()
        existing = self._get_by_sha256(file_sha256)
        if existing is not None:
            return existing

        document_id = f"doc_{uuid.uuid4().hex[:12]}"
        suffix = ".pdf" if is_pdf else ".txt"
        object_key = f"documents/{document_id}{suffix}"
        stored = self.object_store.put_bytes(
            key=object_key,
            content=content,
            content_type="application/pdf" if is_pdf else content_type,
        )

        page_count = _document_page_count(content, is_pdf=is_pdf)
        blocks = [] if is_pdf else parse_document_bytes(
            document_id=document_id,
            filename=filename,
            content=content,
            content_type=content_type,
        )
        text_chars = sum(len(block.text) for block in blocks)
        asset = DocumentAsset(
            document_id=document_id,
            filename=filename,
            content_type="application/pdf" if is_pdf else content_type,
            file_sha256=file_sha256,
            storage_uri=stored.uri,
            file_size_bytes=len(content),
            page_count=page_count,
            text_chars=text_chars,
            block_count=len(blocks),
            source_url=source_url,
            created_at=datetime.now(timezone.utc).isoformat(),
            blocks=blocks,
        )
        self._save_asset(asset)
        return asset

    def ensure_parsed(
        self,
        document_id: str,
        *,
        on_progress: Callable[[int, int, int], None] | None = None,
    ) -> list[DocumentBlock]:
        """Parse an uploaded document on demand and retain the extracted blocks."""
        asset = self.get(document_id)
        if asset.block_count:
            if on_progress is not None:
                on_progress(asset.page_count, asset.page_count, asset.block_count)
            return self.get_blocks(document_id)
        content, content_type, filename = self.get_file(document_id)
        blocks = parse_document_bytes(
            document_id=document_id,
            filename=filename,
            content=content,
            content_type=content_type,
            on_progress=on_progress,
        )
        parsed = asset.model_copy(
            update={
                "text_chars": sum(len(block.text) for block in blocks),
                "block_count": len(blocks),
                "blocks": blocks,
            }
        )
        self._save_asset(parsed)
        return blocks

    def prepare_for_retrieval(
        self,
        document_id: str,
        *,
        chunk_indexer: PostgresChunkIndexer | None = None,
        on_progress: Callable[[int, int, int], None] | None = None,
    ) -> DocumentAsset:
        """Parse one asset and persist its searchable chunks in PostgreSQL."""
        asset = self.get(document_id)
        configured_indexer = chunk_indexer or self.chunk_indexer
        if not self.use_database and configured_indexer is None:
            self.ensure_parsed(document_id, on_progress=on_progress)
            return self.get(document_id)
        indexer = configured_indexer or PostgresChunkIndexer()
        try:
            if asset.storage_uri.startswith("file://"):
                normalize = getattr(indexer, "normalize_legacy_bbox_contract", None)
                if callable(normalize):
                    normalize(document_id)
            indexed_count = indexer.indexed_chunk_count(document_id)
            if asset.storage_uri.startswith("file://") and indexed_count > 0:
                if on_progress is not None:
                    on_progress(asset.page_count, asset.page_count, indexed_count)
                return asset
            blocks = self.ensure_parsed(document_id, on_progress=on_progress)
            if indexer.indexed_chunk_count(document_id) != len(blocks):
                indexed_count = indexer.index_chunks(
                    registration=_prospectus_registration(asset),
                    chunks=_document_chunks(asset, blocks),
                    processed_path=f"document-assets/{document_id}",
                )
            else:
                indexed_count = len(blocks)
        finally:
            if configured_indexer is None:
                indexer.close()
        if indexed_count != len(blocks):
            raise RuntimeError(
                f"PostgreSQL indexed {indexed_count} chunks for {len(blocks)} document blocks."
            )
        return self.get(document_id)

    def get(self, document_id: str) -> DocumentAsset:
        if self.use_database:
            with self.session_factory() as session:
                row = session.get(DocumentAssetRow, document_id)
                if row is not None:
                    return _asset_from_row(row)
                registered = session.get(ProspectusDocumentRow, document_id)
            if registered is None:
                raise KeyError(document_id)
            return _asset_from_registered_row(
                registered,
                block_count=self._indexed_chunk_count(document_id),
            )
        item = self._load_all().get(document_id)
        if item is None:
            raise KeyError(document_id)
        return DocumentAsset.model_validate(item)

    def get_file(self, document_id: str) -> tuple[bytes, str, str]:
        """Return (content, content_type, filename) for the original uploaded file."""
        asset = self.get(document_id)
        if asset.storage_uri.startswith("file://"):
            path = Path(unquote(urlparse(asset.storage_uri).path))
            return path.read_bytes(), asset.content_type, asset.filename
        suffix = ".pdf" if asset.content_type == "application/pdf" else ".txt"
        key = f"documents/{document_id}{suffix}"
        content = self.object_store.get_bytes(key=key)
        return content, asset.content_type, asset.filename

    def list_assets(self) -> list[DocumentAsset]:
        if self.use_database:
            with self.session_factory() as session:
                rows = session.scalars(
                    select(DocumentAssetRow).order_by(DocumentAssetRow.created_at.desc())
                ).all()
                registered_rows = session.scalars(
                    select(ProspectusDocumentRow).order_by(ProspectusDocumentRow.created_at.desc())
                ).all()
            assets: list[DocumentAsset] = []
            seen_ids: set[str] = set()
            seen_hashes: set[str] = set()
            for registered in registered_rows:
                block_count = self._indexed_chunk_count(registered.document_id)
                asset = _asset_from_registered_row(registered, block_count=block_count)
                assets.append(asset)
                seen_ids.add(asset.document_id)
                seen_hashes.add(asset.file_sha256)
            for row in rows:
                asset = _asset_from_row(row, include_blocks=False)
                if asset.document_id in seen_ids or asset.file_sha256 in seen_hashes:
                    continue
                assets.append(asset)
                seen_ids.add(asset.document_id)
                seen_hashes.add(asset.file_sha256)
            assets.sort(key=lambda asset: asset.created_at, reverse=True)
            return assets
        items = []
        for item in self._load_all().values():
            data = dict(item)
            data["blocks"] = []
            items.append(DocumentAsset.model_validate(data))
        items.sort(key=lambda asset: asset.created_at, reverse=True)
        return items

    def get_text(self, document_id: str) -> str:
        return "\n\n".join(block.text for block in self.get_blocks(document_id))

    def get_blocks(self, document_id: str) -> list[DocumentBlock]:
        if self.use_database:
            with self.session_factory() as session:
                rows = session.scalars(
                    select(DocumentBlockRow)
                    .where(DocumentBlockRow.document_id == document_id)
                    .order_by(DocumentBlockRow.page_number, DocumentBlockRow.block_id)
                ).all()
                if not rows:
                    if session.get(DocumentAssetRow, document_id) is None:
                        registered = session.get(ProspectusDocumentRow, document_id)
                        if registered is None:
                            raise KeyError(document_id)
                        return self._indexed_document_blocks(document_id)
                return [_block_from_row(row) for row in rows]
        item = self._load_all().get(document_id)
        if item is None:
            raise KeyError(document_id)
        return [DocumentBlock.model_validate(block) for block in item.get("blocks", [])]

    def _get_by_sha256(self, file_sha256: str) -> DocumentAsset | None:
        if self.use_database:
            with self.session_factory() as session:
                registered = session.scalar(
                    select(ProspectusDocumentRow).where(
                        ProspectusDocumentRow.file_sha256 == file_sha256
                    )
                )
                row = session.scalar(
                    select(DocumentAssetRow).where(DocumentAssetRow.file_sha256 == file_sha256)
                )
            if registered is not None:
                block_count = self._indexed_chunk_count(registered.document_id)
                if block_count > 0:
                    return _asset_from_registered_row(registered, block_count=block_count)
            return _asset_from_row(row) if row else None
        for item in self._load_all().values():
            if item["file_sha256"] == file_sha256:
                return DocumentAsset.model_validate(item)
        return None

    def _indexed_chunk_count(self, document_id: str) -> int:
        indexer = self.chunk_indexer or PostgresChunkIndexer()
        try:
            return indexer.indexed_chunk_count(document_id)
        finally:
            if self.chunk_indexer is None:
                indexer.close()

    def _indexed_document_blocks(self, document_id: str) -> list[DocumentBlock]:
        indexer = self.chunk_indexer or PostgresChunkIndexer()
        try:
            chunks = indexer.list_document_chunks(document_id)
        finally:
            if self.chunk_indexer is None:
                indexer.close()
        return [_block_from_indexed_chunk(chunk) for chunk in chunks]

    def _save_asset(self, asset: DocumentAsset) -> None:
        if self.use_database:
            with self.session_factory() as session:
                _upsert_asset(session, asset)
                session.commit()
            return
        data = self._load_all()
        item = asset.model_dump()
        item["blocks"] = [block.model_dump() for block in asset.blocks]
        data[asset.document_id] = item
        self._save_all(data)

    def _load_all(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}

    def _save_all(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def parse_document_bytes(
    *,
    document_id: str,
    filename: str,
    content: bytes,
    content_type: str,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> list[DocumentBlock]:
    if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
        return _parse_pdf(document_id, content, on_progress=on_progress)
    text = content.decode("utf-8", errors="replace")
    blocks = _parse_text(document_id, text)
    if on_progress is not None:
        on_progress(1, 1, len(blocks))
    return blocks


def _document_page_count(content: bytes, *, is_pdf: bool) -> int:
    if not is_pdf:
        return 1
    with fitz.open(stream=content, filetype="pdf") as pdf:
        return pdf.page_count


def _parse_pdf(
    document_id: str,
    content: bytes,
    *,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    char_cursor = 0
    with fitz.open(stream=content, filetype="pdf") as pdf:
        total_pages = pdf.page_count
        for page_index, page in enumerate(pdf, start=1):
            text_page = page.get_text("dict")
            section_path = "招股书正文"
            for block_index, raw_block in enumerate(text_page.get("blocks", []), start=1):
                if raw_block.get("type") != 0:
                    continue
                text = _text_from_pdf_block(raw_block)
                if not text:
                    continue
                section_path = _detect_section(text, section_path)
                source_hash = hashlib.sha256(
                    f"{document_id}|{page_index}|{block_index}|{text}".encode("utf-8")
                ).hexdigest()
                char_start = char_cursor
                char_end = char_start + len(text)
                char_cursor = char_end + 1
                blocks.append(
                    DocumentBlock(
                        block_id=f"block_{document_id}_{page_index:04d}_{block_index:03d}",
                        document_id=document_id,
                        page_number=page_index,
                        section_path=section_path,
                        text=text,
                        bbox=[float(value) for value in raw_block.get("bbox", [0, 0, 0, 0])],
                        char_start=char_start,
                        char_end=char_end,
                        source_hash=source_hash,
                    )
                )
            if on_progress is not None:
                on_progress(page_index, total_pages, len(blocks))
    if not blocks:
        raise ValueError("PDF contains no extractable text blocks.")
    return blocks


def _parse_text(document_id: str, text: str, *, chunk_size: int = 900) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    cursor = 0
    section_path = "招股书正文"
    chunks = [text[start : start + chunk_size].strip() for start in range(0, len(text), chunk_size)]
    for idx, chunk in enumerate([item for item in chunks if item], start=1):
        section_path = _detect_section(chunk, section_path)
        source_hash = hashlib.sha256(f"{document_id}|{idx}|{chunk}".encode("utf-8")).hexdigest()
        char_start = cursor
        char_end = char_start + len(chunk)
        cursor = char_end + 1
        blocks.append(
            DocumentBlock(
                block_id=f"block_{document_id}_{idx:04d}_001",
                document_id=document_id,
                page_number=idx,
                section_path=section_path,
                text=chunk,
                bbox=[0.0, 0.0, 595.0, 842.0],
                char_start=char_start,
                char_end=char_end,
                source_hash=source_hash,
            )
        )
    return blocks


def _text_from_pdf_block(raw_block: dict[str, Any]) -> str:
    lines = []
    for line in raw_block.get("lines", []):
        spans = [span.get("text", "") for span in line.get("spans", [])]
        line_text = "".join(spans).strip()
        if line_text:
            lines.append(line_text)
    return "\n".join(lines).strip()


def _detect_section(text: str, current: str) -> str:
    head = text[:100]
    for marker in ["风险因素", "关联交易", "财务资料", "业务", "历史及公司架构"]:
        if marker in head:
            return marker
    return current


def process_prospectus_document(
    request: ProspectusPipelineRequest,
    *,
    ingestion_repository: ProspectusIngestionRepository | None = None,
    chunk_indexer: ChunkIndexer | None = None,
    image_describer: ImageDescriber | None = None,
) -> ProspectusPipelineResult:
    """Run the canonical MinerU prospectus pipeline into PostgreSQL."""
    settings = load_rag_pipeline_settings(request.config_path)
    ingestion = ingestion_repository or ProspectusIngestionRepository()
    registration = ingestion.register(
        ProspectusRegisterRequest(
            source_path=request.source_path,
            content=request.content,
            original_filename=request.original_filename,
            doc_key=request.doc_key,
            source_url=request.source_url,
        )
    )

    processed_root = Path(request.processed_root).expanduser().resolve()
    parsed = PdfProcessorFactory().create(settings.pdf_processor.name).process(
        build_pdf_process_request(
            doc_key=registration.doc_key,
            document_id=registration.document_id,
            pdf_path=registration.raw_pdf_path,
            processed_root=processed_root,
            settings=settings.pdf_processor,
        )
    )
    chunk_output_dir = (
        Path(request.chunk_output_dir).expanduser().resolve()
        if request.chunk_output_dir is not None
        else processed_root / registration.document_id / "chunks" / settings.chunker.name
    )
    active_image_describer = (
        image_describer
        if image_describer is not None
        else VLLMImageDescriber()
        if request.enable_image_vlm
        else None
    )
    chunked = ChunkerFactory(image_describer=active_image_describer).create(
        settings.chunker.name
    ).chunk(
        build_chunk_request(
            doc_key=registration.doc_key,
            document_id=registration.document_id,
            content_list_path=parsed.content_list_path,
            output_dir=chunk_output_dir,
            settings=settings.chunker,
        )
    )

    indexed_count: int | None = None
    if request.index_chunks:
        indexer = chunk_indexer or create_chunk_indexer(
            request.index_backend,
            schema=request.index_schema,
        )
        try:
            indexed_count = indexer.index_chunks(
                registration=registration,
                chunks=chunked.chunks,
                processed_path=_project_relative_path(parsed.output_dir),
            )
        finally:
            if chunk_indexer is None and hasattr(indexer, "close"):
                indexer.close()  # type: ignore[attr-defined]

    return ProspectusPipelineResult(
        registration=registration,
        parsed=parsed,
        chunked=chunked,
        indexed_chunk_count=indexed_count,
    )


def register_uploaded_prospectus(
    *,
    filename: str,
    content: bytes,
    source_url: str | None = None,
) -> ProspectusRegistration:
    """Quickly persist an uploaded PDF before asynchronous MinerU processing."""
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Document exceeds 50MB upload limit.")
    if not filename.lower().endswith(".pdf"):
        raise ValueError("Only PDF prospectus assets are supported.")
    file_sha256 = hashlib.sha256(content).hexdigest()
    return ProspectusIngestionRepository().register(
        ProspectusRegisterRequest(
            content=content,
            original_filename=filename,
            doc_key=f"{Path(filename).stem}_{file_sha256[:8]}",
            source_url=source_url,
        )
    )


def process_registered_prospectus(document_id: str) -> None:
    """Run one registered PDF through MinerU/chunking/PostgreSQL off-request."""
    with _ACTIVE_PROSPECTUS_PIPELINES_LOCK:
        if document_id in _ACTIVE_PROSPECTUS_PIPELINES:
            return
        _ACTIVE_PROSPECTUS_PIPELINES.add(document_id)
    ingestion = ProspectusIngestionRepository()
    try:
        registration = ingestion.get(document_id)
        _set_prospectus_status(document_id, "processing")
        process_prospectus_document(
            ProspectusPipelineRequest(
                source_path=registration.raw_pdf_path,
                original_filename=registration.original_filename,
                doc_key=registration.doc_key,
                source_url=registration.source_url,
            ),
            ingestion_repository=ingestion,
        )
        _set_prospectus_status(document_id, "indexed")
    except Exception:
        _set_prospectus_status(document_id, "failed")
        logger.exception("Asynchronous MinerU pipeline failed for %s", document_id)
    finally:
        with _ACTIVE_PROSPECTUS_PIPELINES_LOCK:
            _ACTIVE_PROSPECTUS_PIPELINES.discard(document_id)


def _set_prospectus_status(document_id: str, status: str) -> None:
    with SessionLocal() as session:
        row = session.get(ProspectusDocumentRow, document_id)
        if row is not None:
            row.status = status
            session.commit()


def get_default_document_repository() -> DocumentRepository:
    return DocumentRepository()


def _project_relative_path(path: Path | str) -> str:
    resolved = Path(path).expanduser().resolve()
    try:
        return str(resolved.relative_to(BACKEND_ROOT.parent))
    except ValueError:
        return str(resolved)


def _prospectus_registration(asset: DocumentAsset) -> ProspectusRegistration:
    return ProspectusRegistration(
        document_id=asset.document_id,
        doc_key=asset.document_id,
        original_filename=asset.filename,
        raw_pdf_path=Path(asset.storage_uri),
        content_type=asset.content_type,
        file_sha256=asset.file_sha256,
        file_size_bytes=asset.file_size_bytes,
        source_url=asset.source_url,
        status="indexed",
    )


def _document_chunks(
    asset: DocumentAsset,
    blocks: list[DocumentBlock],
) -> list[DocumentChunk]:
    return [
        DocumentChunk(
            chunk_id=block.block_id,
            document_id=block.document_id,
            doc_key=asset.document_id,
            chunk_type=block.block_type,
            text=block.text,
            page_idx=block.page_number,
            source_file=asset.filename,
            source_item_indices=[index],
            bbox_refs=[
                BBoxRef(
                    page_idx=block.page_number,
                    bbox=block.bbox,
                    source_item_index=index,
                )
            ],
            merged_bboxes=[MergedBBox(page_idx=block.page_number, bbox=block.bbox)],
            metadata={
                "section_path": block.section_path,
                "char_start": block.char_start,
                "char_end": block.char_end,
                "source_hash": block.source_hash,
                "parser_version": block.parser_version,
                "bbox_coordinate_space": "pdf_points",
            },
        )
        for index, block in enumerate(blocks)
    ]


def _upsert_asset(session: Session, asset: DocumentAsset) -> None:
    existing = session.get(DocumentAssetRow, asset.document_id)
    if existing is not None:
        session.delete(existing)
        session.flush()
    row = DocumentAssetRow(
        document_id=asset.document_id,
        filename=asset.filename,
        content_type=asset.content_type,
        file_sha256=asset.file_sha256,
        storage_uri=asset.storage_uri,
        file_size_bytes=asset.file_size_bytes,
        page_count=asset.page_count,
        text_chars=asset.text_chars,
        block_count=asset.block_count,
        source_url=asset.source_url,
    )
    row.blocks = [
        DocumentBlockRow(
            block_id=block.block_id,
            document_id=block.document_id,
            page_number=block.page_number,
            section_path=block.section_path,
            block_type=block.block_type,
            text=block.text,
            bbox=block.bbox,
            char_start=block.char_start,
            char_end=block.char_end,
            source_hash=block.source_hash,
            parser_version=block.parser_version,
        )
        for block in asset.blocks
    ]
    session.add(row)


def _asset_from_row(row: DocumentAssetRow, include_blocks: bool = True) -> DocumentAsset:
    return DocumentAsset(
        document_id=row.document_id,
        filename=row.filename,
        content_type=row.content_type,
        file_sha256=row.file_sha256,
        storage_uri=row.storage_uri,
        file_size_bytes=row.file_size_bytes,
        page_count=row.page_count,
        text_chars=row.text_chars,
        block_count=row.block_count,
        processing_status="ready" if row.block_count > 0 else "uploaded",
        source_url=row.source_url,
        created_at=row.created_at.isoformat() if hasattr(row.created_at, "isoformat") else str(row.created_at),
        blocks=[] if not include_blocks else [_block_from_row(block) for block in row.blocks],
    )


def _asset_from_registered_row(
    row: ProspectusDocumentRow,
    *,
    block_count: int,
) -> DocumentAsset:
    raw_path = Path(row.raw_pdf_path)
    if not raw_path.is_absolute():
        raw_path = (BACKEND_ROOT.parent / raw_path).resolve()
    page_count = 0
    if raw_path.exists():
        with fitz.open(raw_path) as pdf:
            page_count = pdf.page_count
    return DocumentAsset(
        document_id=row.document_id,
        filename=row.original_filename,
        content_type=row.content_type,
        file_sha256=row.file_sha256,
        storage_uri=raw_path.as_uri(),
        file_size_bytes=row.file_size_bytes,
        page_count=page_count,
        text_chars=0,
        block_count=block_count,
        processing_status=(
            "ready"
            if block_count > 0 or row.status == "indexed"
            else "failed"
            if row.status == "failed"
            else "processing"
            if row.status == "processing"
            else "queued"
        ),
        source_url=row.source_url,
        created_at=row.created_at.isoformat()
        if hasattr(row.created_at, "isoformat")
        else str(row.created_at),
        blocks=[],
    )


def _block_from_indexed_chunk(chunk: Any) -> DocumentBlock:
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    coordinate_space = str(metadata.get("bbox_coordinate_space") or "pdf_points")
    if coordinate_space not in {"pdf_points", "normalized_1000"}:
        coordinate_space = "pdf_points"
    bbox = next(
        (
            item.get("bbox", [])
            for item in chunk.merged_bboxes
            if isinstance(item, dict) and int(item.get("page_idx", -1)) == chunk.page_idx
        ),
        [],
    )
    text = str(chunk.text)
    return DocumentBlock(
        block_id=chunk.chunk_id,
        document_id=chunk.document_id,
        page_number=chunk.page_idx,
        section_path=str(metadata.get("section_path") or chunk.chunk_type),
        block_type=chunk.chunk_type,
        text=text,
        bbox=[float(value) for value in bbox],
        bbox_coordinate_space=coordinate_space,
        char_start=int(metadata.get("char_start") or 0),
        char_end=int(metadata.get("char_end") or len(text)),
        source_hash=str(metadata.get("source_hash") or hashlib.sha256(text.encode()).hexdigest()),
        parser_version=str(metadata.get("parser_version") or "postgres-index-v1"),
    )


def _block_from_row(row: DocumentBlockRow) -> DocumentBlock:
    return DocumentBlock(
        block_id=row.block_id,
        document_id=row.document_id,
        page_number=row.page_number,
        section_path=row.section_path,
        block_type=row.block_type,
        text=row.text,
        bbox=list(row.bbox or []),
        char_start=row.char_start,
        char_end=row.char_end,
        source_hash=row.source_hash,
        parser_version=row.parser_version,
    )
