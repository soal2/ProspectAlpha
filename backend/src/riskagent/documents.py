"""Prospectus RAG pipeline orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from riskagent.ingestion import (
    ProspectusIngestionRepository,
    ProspectusRegisterRequest,
    ProspectusRegistration,
)
from riskagent.parsing import (
    ChunkResult,
    ChunkerFactory,
    ImageDescriber,
    PdfProcessResult,
    PdfProcessorFactory,
    build_chunk_request,
    build_pdf_process_request,
    load_rag_pipeline_settings,
)
from riskagent.providers.vllm import VLLMImageDescriber
from riskagent.retrieval import ChunkIndexer, create_chunk_indexer

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = BACKEND_ROOT / "data"


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


def process_prospectus_document(
    request: ProspectusPipelineRequest,
    *,
    ingestion_repository: ProspectusIngestionRepository | None = None,
    chunk_indexer: ChunkIndexer | None = None,
    image_describer: ImageDescriber | None = None,
) -> ProspectusPipelineResult:
    """Run registration, MinerU parsing, chunking, and optional chunk indexing.

    This is the canonical prospectus RAG workflow entrypoint. Scripts should call
    this function instead of duplicating the pipeline steps.
    """
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
    chunked = ChunkerFactory(image_describer=active_image_describer).create(settings.chunker.name).chunk(
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


def _project_relative_path(path: Path | str) -> str:
    resolved = Path(path).expanduser().resolve()
    try:
        return str(resolved.relative_to(BACKEND_ROOT.parent))
    except ValueError:
        return str(resolved)
