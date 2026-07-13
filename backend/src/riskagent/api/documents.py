"""Prospectus upload, metadata, and original-PDF routes."""

from __future__ import annotations

from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from riskagent.documents import ProspectusPipelineRequest, process_prospectus_document
from riskagent.ingestion import ProspectusIngestionRepository, ProspectusRegistration


router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentResponse(BaseModel):
    document_id: str
    filename: str
    content_type: str
    file_sha256: str
    storage_uri: str
    file_size_bytes: int
    page_count: int
    text_chars: int
    block_count: int
    source_url: str | None


def _repo() -> ProspectusIngestionRepository:
    return ProspectusIngestionRepository()


def _document_response(
    registration: ProspectusRegistration,
    *,
    text_chars: int = 0,
    block_count: int = 0,
) -> DocumentResponse:
    with fitz.open(registration.raw_pdf_path) as pdf:
        page_count = len(pdf)
    return DocumentResponse(
        document_id=registration.document_id,
        filename=registration.original_filename,
        content_type=registration.content_type,
        file_sha256=registration.file_sha256,
        storage_uri=f"/documents/{registration.document_id}/file",
        file_size_bytes=registration.file_size_bytes,
        page_count=page_count,
        text_chars=text_chars,
        block_count=block_count,
        source_url=registration.source_url,
    )


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def upload_document(
    file: UploadFile = File(...),
    source_url: str | None = Form(default=None),
) -> DocumentResponse:
    filename = file.filename or "prospectus.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    try:
        result = process_prospectus_document(
            ProspectusPipelineRequest(
                content=file.file.read(),
                original_filename=filename,
                source_url=source_url,
            )
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _document_response(
        result.registration,
        text_chars=sum(len(chunk.text) for chunk in result.chunked.chunks),
        block_count=len(result.chunked.chunks),
    )


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(document_id: str) -> DocumentResponse:
    try:
        return _document_response(_repo().get(document_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc


@router.get("/{document_id}/file", response_class=FileResponse)
def get_document_file(document_id: str) -> FileResponse:
    try:
        registration = _repo().get(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    pdf_path = Path(registration.raw_pdf_path)
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="PDF file not found")
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=registration.original_filename,
        content_disposition_type="inline",
    )
