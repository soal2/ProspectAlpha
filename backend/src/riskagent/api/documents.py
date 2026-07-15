"""Document asset routes."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from urllib.parse import quote

import fitz

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from riskagent.document_qa import DocumentQuestionAnswerer, DocumentQuestionError
from riskagent.documents import (
    DocumentAsset,
    DocumentBlock,
    DocumentCreate,
    DocumentRepository,
    get_default_document_repository,
    process_registered_prospectus,
    register_uploaded_prospectus,
)
from riskagent.ingestion import ProspectusRegistration


router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)


def _repo() -> DocumentRepository:
    return get_default_document_repository()


def _answerer() -> DocumentQuestionAnswerer:
    return DocumentQuestionAnswerer(repository=_repo())


class DocumentQuestionRequest(BaseModel):
    question: str
    top_k: int = 6


@router.post("", response_model=DocumentAsset, status_code=status.HTTP_201_CREATED)
def create_document(payload: DocumentCreate) -> DocumentAsset:
    try:
        return _repo().create(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/upload", response_model=DocumentAsset, status_code=status.HTTP_201_CREATED)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source_url: str | None = Form(default=None),
) -> DocumentAsset:
    content = await file.read()
    try:
        repo = _repo()
        filename = file.filename or "prospectus.pdf"
        if repo.use_database:
            registration = await asyncio.to_thread(
                register_uploaded_prospectus,
                filename=filename,
                content=content,
                source_url=source_url,
            )
            asset = await asyncio.to_thread(repo.get, registration.document_id)
            if asset.block_count <= 0 and asset.processing_status != "processing":
                background_tasks.add_task(
                    process_registered_prospectus,
                    registration.document_id,
                )
            return asset
        asset = await asyncio.to_thread(
            repo.create_from_bytes,
            filename=filename,
            content=content,
            content_type=file.content_type or "application/octet-stream",
            source_url=source_url,
        )
        return await asyncio.to_thread(repo.prepare_for_retrieval, asset.document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("", response_model=list[DocumentAsset])
def list_documents() -> list[DocumentAsset]:
    return _repo().list_assets()


@router.get("/{document_id}/file")
def get_document_file(document_id: str) -> Response:
    repo = _repo()
    try:
        if hasattr(repo, "get_file"):
            content, content_type, filename = repo.get_file(document_id)
        else:
            registration = repo.get(document_id)
            content = registration.raw_pdf_path.read_bytes()
            content_type = registration.content_type
            filename = registration.original_filename
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document file not found") from exc
    ascii_name = filename.encode("ascii", errors="ignore").decode() or "document.pdf"
    disposition = f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": disposition},
    )


@router.get("/{document_id}", response_model=DocumentAsset)
def get_document(document_id: str) -> DocumentAsset:
    try:
        document = _repo().get(document_id)
        if isinstance(document, ProspectusRegistration):
            with fitz.open(document.raw_pdf_path) as pdf:
                page_count = pdf.page_count
            return DocumentAsset(
                document_id=document.document_id,
                filename=document.original_filename,
                content_type=document.content_type,
                file_sha256=document.file_sha256,
                storage_uri=f"/documents/{document.document_id}/file",
                file_size_bytes=document.file_size_bytes,
                page_count=page_count,
                text_chars=0,
                block_count=0,
                source_url=document.source_url,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        return document
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found") from exc


@router.get("/{document_id}/blocks", response_model=list[DocumentBlock])
def get_document_blocks(document_id: str) -> list[DocumentBlock]:
    try:
        return _repo().get_blocks(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found") from exc


@router.post("/{document_id}/ask", response_class=StreamingResponse)
async def ask_document(
    document_id: str,
    payload: DocumentQuestionRequest,
) -> StreamingResponse:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must not be empty")
    if not 1 <= payload.top_k <= 10:
        raise HTTPException(status_code=422, detail="top_k must be between 1 and 10")
    answerer = _answerer()
    try:
        context = await asyncio.to_thread(
            answerer.retrieve,
            document_id=document_id,
            question=question,
            top_k=payload.top_k,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except DocumentQuestionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Document retrieval failed for %s", document_id)
        raise HTTPException(status_code=503, detail="Document retrieval failed") from exc

    async def event_source():
        try:
            async for delta in answerer.answer_stream(context):
                yield _sse({"type": "delta", "delta": delta})
            yield _sse(
                {
                    "type": "done",
                    "citations": [citation.__dict__ for citation in context.citations],
                }
            )
        except Exception:
            logger.exception("Document answer generation failed for %s", document_id)
            yield _sse({"type": "error", "message": "Document answer generation failed"})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
