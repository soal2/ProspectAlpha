from __future__ import annotations

from pathlib import Path

from riskagent.documents import ProspectusPipelineRequest, process_prospectus_document
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
    source_pdf = Path("data/raw/prospectus/ysyy.pdf")
    indexer = create_chunk_indexer(
        "postgres",
        schema="rag",
        embedding_client=ProgressEmbeddingClient(),
    )
    try:
        print(f"pipeline_start {source_pdf}", flush=True)
        result = process_prospectus_document(
            ProspectusPipelineRequest(
                source_path=source_pdf,
                doc_key="ysyy",
                config_path="configs/rag.yaml",
                processed_root="data/processed",
                enable_image_vlm=True,
                index_chunks=True,
            ),
            chunk_indexer=indexer,
            image_describer=ProgressImageDescriber(),
        )
        print(
            f"pipeline_done document_id={result.registration.document_id} "
            f"doc_key={result.registration.doc_key} raw={result.registration.raw_pdf_path}",
            flush=True,
        )
        print(f"parsed {result.parsed.content_list_path}", flush=True)
        print(
            f"chunked {result.chunks_path} total={len(result.chunked.chunks)} "
            f"indexed={result.indexed_chunk_count}",
            flush=True,
        )
        for mode in ["vector", "full_text", "hybrid"]:
            results = indexer.search(
                query="BCR-ABL 是什么",
                document_id=result.registration.document_id,
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
