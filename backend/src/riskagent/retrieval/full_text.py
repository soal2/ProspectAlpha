"""Full-text retrieval boundary for indexed prospectus chunks."""

from __future__ import annotations

from riskagent.retrieval.index import ChunkSearchResult, PostgresChunkIndexer


def full_text_search(
    *,
    query: str,
    document_id: str,
    limit: int = 5,
    indexer: PostgresChunkIndexer | None = None,
) -> list[ChunkSearchResult]:
    active_indexer = indexer or PostgresChunkIndexer(ensure=False)
    try:
        return active_indexer.full_text_search(
            query=query,
            document_id=document_id,
            limit=limit,
        )
    finally:
        if indexer is None:
            active_indexer.close()
