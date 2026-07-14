"""Document asset storage and PDF parsing for the analysis pipeline."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz
import yaml
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from riskagent.database import Base, SessionLocal, init_db
from riskagent.models import DocumentAssetRow, DocumentBlockRow
from riskagent.providers.object_store import ObjectStore, get_default_object_store

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = BACKEND_ROOT / "data"
DEFAULT_DOCUMENTS_PATH = DATA_ROOT / "processed" / "documents.yaml"
DEFAULT_DOCUMENT_STORAGE_DIR = DATA_ROOT / "raw" / "documents"
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
PARSER_VERSION = "pymupdf-v1"


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
    ) -> None:
        self.path = Path(path or os.getenv("DOCUMENTS_PATH") or DEFAULT_DOCUMENTS_PATH)
        self.storage_dir = Path(
            storage_dir or os.getenv("DOCUMENT_STORAGE_DIR") or DEFAULT_DOCUMENT_STORAGE_DIR
        )
        self.session_factory = session_factory
        self.use_database = use_database if use_database is not None else os.getenv("USE_DATABASE", "1") != "0"
        self.object_store = object_store or get_default_object_store()
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

        blocks = parse_document_bytes(
            document_id=document_id,
            filename=filename,
            content=content,
            content_type="application/pdf" if is_pdf else content_type,
        )
        text_chars = sum(len(block.text) for block in blocks)
        page_count = max((block.page_number for block in blocks), default=0)
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

    def get(self, document_id: str) -> DocumentAsset:
        if self.use_database:
            with self.session_factory() as session:
                row = session.get(DocumentAssetRow, document_id)
                if row is None:
                    raise KeyError(document_id)
                return _asset_from_row(row)
        item = self._load_all().get(document_id)
        if item is None:
            raise KeyError(document_id)
        return DocumentAsset.model_validate(item)

    def get_file(self, document_id: str) -> tuple[bytes, str, str]:
        """Return (content, content_type, filename) for the original uploaded file."""
        asset = self.get(document_id)
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
                return [_asset_from_row(row, include_blocks=False) for row in rows]
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
                        raise KeyError(document_id)
                return [_block_from_row(row) for row in rows]
        item = self._load_all().get(document_id)
        if item is None:
            raise KeyError(document_id)
        return [DocumentBlock.model_validate(block) for block in item.get("blocks", [])]

    def _get_by_sha256(self, file_sha256: str) -> DocumentAsset | None:
        if self.use_database:
            with self.session_factory() as session:
                row = session.scalar(
                    select(DocumentAssetRow).where(DocumentAssetRow.file_sha256 == file_sha256)
                )
                return _asset_from_row(row) if row else None
        for item in self._load_all().values():
            if item["file_sha256"] == file_sha256:
                return DocumentAsset.model_validate(item)
        return None

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
) -> list[DocumentBlock]:
    if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
        return _parse_pdf(document_id, content)
    text = content.decode("utf-8", errors="replace")
    return _parse_text(document_id, text)


def _parse_pdf(document_id: str, content: bytes) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    char_cursor = 0
    with fitz.open(stream=content, filetype="pdf") as pdf:
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


def get_default_document_repository() -> DocumentRepository:
    return DocumentRepository()


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
        source_url=row.source_url,
        created_at=row.created_at.isoformat() if hasattr(row.created_at, "isoformat") else str(row.created_at),
        blocks=[] if not include_blocks else [_block_from_row(block) for block in row.blocks],
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
