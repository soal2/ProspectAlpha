"""Evidence retrieval boundary for source-grounded risk analysis."""

from riskagent.retrieval.evidence_store import (
    EvidenceItem,
    EvidenceStore,
    compute_content_sha256,
    compute_quality_score,
    ensure_evidence_schema,
)
from riskagent.retrieval.hybrid import prospectus_hybrid_search
from riskagent.retrieval.index import (
    ChunkIndexer,
    ChunkIndexError,
    ChunkSearchResult,
    PostgresChunkIndexer,
    connect_postgres,
    create_chunk_indexer,
    ensure_extensions,
    ensure_schema,
)

__all__ = [
    "ChunkIndexer",
    "ChunkIndexError",
    "ChunkSearchResult",
    "EvidenceItem",
    "EvidenceStore",
    "PostgresChunkIndexer",
    "compute_content_sha256",
    "compute_quality_score",
    "connect_postgres",
    "create_chunk_indexer",
    "ensure_evidence_schema",
    "ensure_extensions",
    "ensure_schema",
    "prospectus_hybrid_search",
]
