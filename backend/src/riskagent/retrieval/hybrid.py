"""PostgreSQL/pgvector hybrid retrieval for indexed prospectus chunks."""

from __future__ import annotations

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
