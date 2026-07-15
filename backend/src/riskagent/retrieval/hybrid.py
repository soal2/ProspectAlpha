"""PostgreSQL/pgvector hybrid retrieval for indexed prospectus chunks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from riskagent.retrieval.index import ChunkSearchResult, PostgresChunkIndexer


def prospectus_hybrid_search(
    *,
    query: str,
    document_id: str,
    limit: int = 5,
    vector_weight: float = 0.75,
    indexer: PostgresChunkIndexer | None = None,
) -> list[ChunkSearchResult]:
    """Search one prospectus in PostgreSQL; cross-document search is forbidden."""
    active_indexer = indexer or PostgresChunkIndexer()
    try:
        return active_indexer.hybrid_search(
            query=query,
            document_id=document_id,
            limit=limit,
            vector_weight=vector_weight,
        )
    finally:
        if indexer is None:
            active_indexer.close()


class IndexableBlock(Protocol):
    block_id: str
    document_id: str
    page_number: int
    section_path: str
    block_type: str
    text: str
    bbox: list[float]
    char_start: int
    char_end: int
    source_hash: str
    parser_version: str


@dataclass
class EvidenceCandidate:
    block_id: str
    document_id: str
    page_number: int
    section_path: str
    text: str
    bbox: list[float]
    char_start: int
    char_end: int
    source_hash: str
    parser_version: str
    score: float
    lexical_score: float
    vector_score: float


class HybridSearchIndex:
    """Compatibility facade backed exclusively by the public PostgreSQL index.

    New analysis code should pass ``document_id`` directly to
    :func:`prospectus_hybrid_search`. The facade remains while the latest Agent
    workflow still supplies a run id; ``index_blocks`` records its document
    mapping but deliberately does not create a second run-scoped/Qdrant index.
    """

    def __init__(self, **_: object) -> None:
        self._documents_by_run: dict[str, str] = {}

    def index_blocks(
        self,
        *,
        run_id: str,
        blocks: list[IndexableBlock],
        replace: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        del replace
        document_ids = {block.document_id for block in blocks if block.document_id}
        if len(document_ids) != 1:
            raise ValueError("Analysis blocks must belong to exactly one indexed document.")
        self._documents_by_run[run_id] = next(iter(document_ids))
        if on_progress is not None:
            on_progress(len(blocks), len(blocks))
        return len(blocks)

    def search(self, *, run_id: str, query: str, limit: int = 5) -> list[EvidenceCandidate]:
        document_id = self._documents_by_run.get(run_id)
        if not document_id:
            raise ValueError(f"No PostgreSQL document mapping registered for run: {run_id}")
        return [_candidate(item) for item in prospectus_hybrid_search(
            query=query,
            document_id=document_id,
            limit=limit,
        )]


def _candidate(item: ChunkSearchResult) -> EvidenceCandidate:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    bbox = next(
        (
            region.get("bbox", [])
            for region in item.merged_bboxes
            if isinstance(region, dict) and int(region.get("page_idx", -1)) == item.page_idx
        ),
        [],
    )
    return EvidenceCandidate(
        block_id=item.chunk_id,
        document_id=item.document_id,
        page_number=item.page_idx,
        section_path=str(metadata.get("section_path") or item.chunk_type),
        text=item.text,
        bbox=[float(value) for value in bbox],
        char_start=int(metadata.get("char_start") or 0),
        char_end=int(metadata.get("char_end") or 0),
        source_hash=str(metadata.get("source_hash") or ""),
        parser_version=str(metadata.get("parser_version") or ""),
        score=item.score,
        lexical_score=item.text_score,
        vector_score=item.vector_score,
    )
