"""Document asset routes."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from riskagent.documents import (
    DocumentAsset,
    DocumentCreate,
    DocumentRepository,
    get_default_document_repository,
)


router = APIRouter(prefix="/documents", tags=["documents"])


def _repo() -> DocumentRepository:
    return get_default_document_repository()


@router.post("", response_model=DocumentAsset, status_code=status.HTTP_201_CREATED)
def create_document(payload: DocumentCreate) -> DocumentAsset:
    try:
        return _repo().create(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/upload", response_model=DocumentAsset, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    source_url: str | None = Form(default=None),
) -> DocumentAsset:
    content = await file.read()
    try:
        return _repo().create_from_bytes(
            filename=file.filename or "prospectus.pdf",
            content=content,
            content_type=file.content_type or "application/octet-stream",
            source_url=source_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{document_id}", response_model=DocumentAsset)
def get_document(document_id: str) -> DocumentAsset:
    try:
        return _repo().get(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found") from exc
