from __future__ import annotations

from collections import Counter
from pathlib import Path

from riskagent.ingestion import ProspectusIngestionRepository, ProspectusRegisterRequest
from riskagent.parsing import (
    ChunkerFactory,
    PdfProcessorFactory,
    build_chunk_request,
    build_pdf_process_request,
    load_rag_pipeline_settings,
)
from riskagent.providers.embedding import get_default_embedding_client
from riskagent.providers.vllm import VLLMImageDescriber
from riskagent.retrieval import create_chunk_indexer


class ProgressImageDescriber:
    def __init__(self) -> None:
        self.inner = VLLMImageDescriber()
        self.count = 0

    def describe(self, image_path, source_item, context=None) -> str:
        self.count += 1
        print(
            f"vlm_image {self.count} page={source_item.get('page_idx')} path={Path(image_path).name}",
            flush=True,
        )
        return self.inner.describe(image_path, source_item, context)


class ProgressEmbeddingClient:
    def __init__(self) -> None:
        self.inner = get_default_embedding_client()
        self.batch_count = 0
        self.text_count = 0

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.batch_count += 1
        self.text_count += len(texts)
        print(
            f"embedding_batch {self.batch_count} size={len(texts)} total={self.text_count}",
            flush=True,
        )
        return self.inner.encode(texts)

    def embed(self, text: str) -> list[float]:
        return self.inner.embed(text)


def main() -> int:
    settings = load_rag_pipeline_settings("configs/rag.yaml")
    source_pdf = Path("data/raw/prospectus/ysyy.pdf")
    processed_root = Path("data/processed")

    print(f"register_start {source_pdf}", flush=True)
    registration = ProspectusIngestionRepository().register(
        ProspectusRegisterRequest(source_path=source_pdf, doc_key="ysyy")
    )
    print(
        f"registered {registration.document_id} {registration.doc_key} {registration.raw_pdf_path}",
        flush=True,
    )

    print("parse_start", flush=True)
    parsed = PdfProcessorFactory().create(settings.pdf_processor.name).process(
        build_pdf_process_request(
            doc_key=registration.doc_key,
            document_id=registration.document_id,
            pdf_path=registration.raw_pdf_path,
            processed_root=processed_root,
            settings=settings.pdf_processor,
        )
    )
    print(f"parsed {parsed.content_list_path}", flush=True)

    print("chunk_start", flush=True)
    chunked = ChunkerFactory(image_describer=ProgressImageDescriber()).create(
        settings.chunker.name
    ).chunk(
        build_chunk_request(
            doc_key=registration.doc_key,
            document_id=registration.document_id,
            content_list_path=parsed.content_list_path,
            output_dir=processed_root / registration.document_id / "chunks" / settings.chunker.name,
            settings=settings.chunker,
        )
    )
    counts = Counter(chunk.chunk_type for chunk in chunked.chunks)
    empty = Counter(chunk.chunk_type for chunk in chunked.chunks if not chunk.text.strip())
    long = [
        chunk
        for chunk in chunked.chunks
        if chunk.chunk_type in {"text", "table"} and len(chunk.text) > settings.chunker.max_chars
    ]
    print(
        f"chunked {chunked.output_path} total={len(chunked.chunks)} "
        f"counts={dict(counts)} empty={dict(empty)} long_text_table={len(long)}",
        flush=True,
    )

    print("index_start", flush=True)
    indexer = create_chunk_indexer(
        "postgres",
        schema="rag",
        embedding_client=ProgressEmbeddingClient(),
    )
    try:
        indexed = indexer.index_chunks(
            registration=registration,
            chunks=chunked.chunks,
            processed_path=str(parsed.output_dir),
        )
        print(f"indexed {indexed}", flush=True)
        for mode in ["vector", "full_text", "hybrid"]:
            results = indexer.search(
                query="BCR-ABL 是什么",
                document_id=registration.document_id,
                mode=mode,
                limit=3,
            )
            summary = [
                (result.chunk_id, result.chunk_type, result.page_idx, round(result.score, 4))
                for result in results
            ]
            print(f"search {mode} {summary}", flush=True)
    finally:
        indexer.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
