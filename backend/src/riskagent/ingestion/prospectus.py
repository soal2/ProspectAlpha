"""Prospectus PDF registration and metadata persistence."""

from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from riskagent.database import BACKEND_ROOT, Base, SessionLocal, init_db, session_scope
from riskagent.models import ProspectusDocumentRow


DEFAULT_RAW_PROSPECTUS_DIR = BACKEND_ROOT / "data" / "raw" / "prospectus"
PDF_CONTENT_TYPE = "application/pdf"


@dataclass(frozen=True)
class ProspectusRegistration:
    document_id: str
    doc_key: str
    original_filename: str
    raw_pdf_path: Path
    content_type: str
    file_sha256: str
    file_size_bytes: int
    source_url: str | None
    status: str


@dataclass(frozen=True)
class ProspectusRegisterRequest:
    source_path: Path | str | None = None
    content: bytes | None = None
    original_filename: str | None = None
    doc_key: str | None = None
    source_url: str | None = None
    content_type: str = PDF_CONTENT_TYPE


class ProspectusIngestionRepository:
    def __init__(
        self,
        *,
        raw_dir: Path | str = DEFAULT_RAW_PROSPECTUS_DIR,
        session_factory: sessionmaker = SessionLocal,
        ensure_schema: bool = True,
    ) -> None:
        self.raw_dir = Path(raw_dir)
        self.session_factory = session_factory
        if ensure_schema:
            bind = getattr(session_factory, "kw", {}).get("bind")
            if bind is None:
                init_db()
            else:
                Base.metadata.create_all(bind=bind)

    def register(self, request: ProspectusRegisterRequest) -> ProspectusRegistration:
        raw_bytes, original_filename, source_path = self._read_request_content(request)
        file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        doc_key = _clean_doc_key(request.doc_key or Path(original_filename).stem)
        raw_pdf_path = (self.raw_dir / f"{doc_key}.pdf").resolve()

        with session_scope(self.session_factory) as session:
            existing = session.scalar(
                select(ProspectusDocumentRow).where(
                    ProspectusDocumentRow.file_sha256 == file_sha256
                )
            )
            if existing is not None:
                return _registration_from_row(existing)

            doc_key_owner = session.scalar(
                select(ProspectusDocumentRow).where(ProspectusDocumentRow.doc_key == doc_key)
            )
            if doc_key_owner is not None:
                raise ValueError(f"doc_key already registered with different content: {doc_key}")

            self._write_canonical_pdf(raw_pdf_path, raw_bytes, source_path)
            row = ProspectusDocumentRow(
                document_id=f"doc_{uuid.uuid4().hex[:12]}",
                doc_key=doc_key,
                original_filename=original_filename,
                raw_pdf_path=_project_relative(raw_pdf_path),
                content_type=request.content_type,
                file_sha256=file_sha256,
                file_size_bytes=len(raw_bytes),
                source_url=request.source_url,
                status="registered",
            )
            session.add(row)
            session.flush()
            return _registration_from_row(row)

    def get_by_doc_key(self, doc_key: str) -> ProspectusRegistration:
        with self.session_factory() as session:
            row = session.scalar(
                select(ProspectusDocumentRow).where(ProspectusDocumentRow.doc_key == doc_key)
            )
            if row is None:
                raise KeyError(doc_key)
            return _registration_from_row(row)

    def _read_request_content(
        self, request: ProspectusRegisterRequest
    ) -> tuple[bytes, str, Path | None]:
        if request.content is not None:
            original_filename = request.original_filename or "prospectus.pdf"
            if not original_filename.lower().endswith(".pdf"):
                raise ValueError("original_filename must end with .pdf")
            return request.content, original_filename, None

        if request.source_path is None:
            raise ValueError("source_path or content is required")

        source_path = Path(request.source_path).expanduser().resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"Prospectus PDF not found: {source_path}")
        if not source_path.is_file():
            raise ValueError(f"Prospectus path is not a file: {source_path}")
        if source_path.suffix.lower() != ".pdf":
            raise ValueError(f"Prospectus path must be a PDF: {source_path}")

        return (
            source_path.read_bytes(),
            request.original_filename or source_path.name,
            source_path,
        )

    def _write_canonical_pdf(
        self, raw_pdf_path: Path, raw_bytes: bytes, source_path: Path | None
    ) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        if source_path is not None and source_path == raw_pdf_path:
            return
        if raw_pdf_path.exists():
            existing_hash = hashlib.sha256(raw_pdf_path.read_bytes()).hexdigest()
            incoming_hash = hashlib.sha256(raw_bytes).hexdigest()
            if existing_hash != incoming_hash:
                raise ValueError(f"Canonical raw PDF already exists with different content: {raw_pdf_path}")
            return
        if source_path is not None:
            shutil.copyfile(source_path, raw_pdf_path)
        else:
            raw_pdf_path.write_bytes(raw_bytes)


def _clean_doc_key(value: str) -> str:
    doc_key = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip()).strip("_").lower()
    if not doc_key:
        raise ValueError("doc_key cannot be empty")
    return doc_key


def _project_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(BACKEND_ROOT.parent))
    except ValueError:
        return str(path.resolve())


def _registration_from_row(row: ProspectusDocumentRow) -> ProspectusRegistration:
    return ProspectusRegistration(
        document_id=row.document_id,
        doc_key=row.doc_key,
        original_filename=row.original_filename,
        raw_pdf_path=(BACKEND_ROOT.parent / row.raw_pdf_path).resolve()
        if not Path(row.raw_pdf_path).is_absolute()
        else Path(row.raw_pdf_path),
        content_type=row.content_type,
        file_sha256=row.file_sha256,
        file_size_bytes=row.file_size_bytes,
        source_url=row.source_url,
        status=row.status,
    )
