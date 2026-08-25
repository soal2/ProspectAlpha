"""Evidence store boundary.

Persistent evidence store for cross-analysis evidence reuse and 100% traceability.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from riskagent.retrieval.index import (
    DEFAULT_INDEX_SCHEMA,
    _quote_ident,
    connect_postgres,
    index_transaction,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class EvidenceItem(BaseModel):
    """Single evidence item linking an analysis finding to a source chunk."""

    evidence_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    chunk_id: str
    document_id: str
    page_idx: int
    content_sha256: str
    snippet: str
    quality_score: float = Field(ge=0.0, le=1.0)
    quality_tier: str  # "direct_citation", "direct_reference", "inference", "indirect_support"
    source_type: str  # "retrieval", "expert_analysis", "cross_validation"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_global: bool = False


# ---------------------------------------------------------------------------
# Quality scoring
# ---------------------------------------------------------------------------


def compute_quality_score(
    is_direct_quote: bool,
    has_cross_validation: bool,
    source_count: int,
    confidence: float,
) -> tuple[float, str]:
    """Compute evidence quality score and tier.

    Returns ``(score, tier)`` where tier is one of:
    - ``direct_citation`` (1.0): direct quote with cross-validation
    - ``direct_reference`` (0.7): direct quote without cross-validation
    - ``inference`` (0.4): derived from analysis, not a direct quote
    - ``indirect_support`` (0.2): tangentially related evidence
    """
    if is_direct_quote and has_cross_validation:
        return 1.0, "direct_citation"
    if is_direct_quote:
        return 0.7, "direct_reference"
    if source_count >= 2 or confidence >= 0.6:
        return 0.4, "inference"
    return 0.2, "indirect_support"


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

_EVIDENCE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {schema}.evidence_items (
    evidence_id    TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL,
    chunk_id       TEXT NOT NULL,
    document_id    TEXT NOT NULL,
    page_idx       INT NOT NULL,
    content_sha256 TEXT NOT NULL,
    snippet        TEXT NOT NULL,
    quality_score  REAL NOT NULL DEFAULT 0.0,
    quality_tier   TEXT NOT NULL DEFAULT 'indirect_support',
    source_type    TEXT NOT NULL DEFAULT 'retrieval',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_global      BOOLEAN NOT NULL DEFAULT FALSE
)
"""

_EVIDENCE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS evidence_items_run_id_idx ON {schema}.evidence_items (run_id)",
    "CREATE INDEX IF NOT EXISTS evidence_items_sha256_idx ON {schema}.evidence_items (content_sha256)",
    "CREATE INDEX IF NOT EXISTS evidence_items_document_id_idx ON {schema}.evidence_items (document_id)",
]

_GLOBAL_EVIDENCE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {schema}.global_evidence (
    evidence_id    TEXT PRIMARY KEY,
    source_run_id  TEXT NOT NULL,
    chunk_id       TEXT NOT NULL,
    document_id    TEXT NOT NULL,
    page_idx       INT NOT NULL,
    content_sha256 TEXT NOT NULL,
    snippet        TEXT NOT NULL,
    quality_score  REAL NOT NULL DEFAULT 0.0,
    quality_tier   TEXT NOT NULL DEFAULT 'indirect_support',
    source_type    TEXT NOT NULL DEFAULT 'retrieval',
    promoted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_GLOBAL_EVIDENCE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS global_evidence_sha256_idx ON {schema}.global_evidence (content_sha256)",
    "CREATE INDEX IF NOT EXISTS global_evidence_document_id_idx ON {schema}.global_evidence (document_id)",
    "CREATE INDEX IF NOT EXISTS global_evidence_source_run_id_idx ON {schema}.global_evidence (source_run_id)",
]


def ensure_evidence_schema(*, schema: str, conn: Any) -> None:
    """Create evidence tables and indexes if they do not exist."""
    schema_sql = _quote_ident(schema)
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_sql}")
        cur.execute(_EVIDENCE_TABLE_SQL.format(schema=schema_sql))
        for index_sql in _EVIDENCE_INDEXES_SQL:
            cur.execute(index_sql.format(schema=schema_sql))
        cur.execute(_GLOBAL_EVIDENCE_TABLE_SQL.format(schema=schema_sql))
        for index_sql in _GLOBAL_EVIDENCE_INDEXES_SQL:
            cur.execute(index_sql.format(schema=schema_sql))
    conn.commit()


# ---------------------------------------------------------------------------
# EvidenceStore
# ---------------------------------------------------------------------------


class EvidenceStore:
    """Persistent evidence store for cross-analysis evidence reuse."""

    def __init__(self, conn: Any = None, *, schema: str = DEFAULT_INDEX_SCHEMA) -> None:
        """Initialize with optional PostgreSQL connection.

        If *conn* is ``None`` a new connection is created lazily on first use.
        """
        self._conn = conn
        self._owns_connection = conn is None
        self.schema = schema

    @property
    def conn(self) -> Any:
        if self._conn is None:
            self._conn = connect_postgres()
        return self._conn

    def close(self) -> None:
        if self._owns_connection and self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- schema bootstrap ---------------------------------------------------

    def ensure_schema(self) -> None:
        """Create evidence tables if they do not yet exist."""
        ensure_evidence_schema(schema=self.schema, conn=self.conn)

    # -- CRUD ---------------------------------------------------------------

    async def store_evidence(self, run_id: str, evidence_items: list[EvidenceItem]) -> None:
        """Store evidence items for an analysis run."""
        schema_sql = _quote_ident(self.schema)
        rows = [
            (
                item.evidence_id,
                item.run_id,
                item.chunk_id,
                item.document_id,
                item.page_idx,
                item.content_sha256,
                item.snippet,
                item.quality_score,
                item.quality_tier,
                item.source_type,
                item.created_at,
                item.is_global,
            )
            for item in evidence_items
        ]

        def _write() -> None:
            with index_transaction(self.conn):
                with self.conn.cursor() as cur:
                    for row in rows:
                        cur.execute(
                            f"""
INSERT INTO {schema_sql}.evidence_items
    (evidence_id, run_id, chunk_id, document_id, page_idx, content_sha256,
     snippet, quality_score, quality_tier, source_type, created_at, is_global)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (evidence_id) DO UPDATE SET
    run_id = EXCLUDED.run_id,
    chunk_id = EXCLUDED.chunk_id,
    document_id = EXCLUDED.document_id,
    page_idx = EXCLUDED.page_idx,
    content_sha256 = EXCLUDED.content_sha256,
    snippet = EXCLUDED.snippet,
    quality_score = EXCLUDED.quality_score,
    quality_tier = EXCLUDED.quality_tier,
    source_type = EXCLUDED.source_type,
    is_global = EXCLUDED.is_global
                            """,
                            row,
                        )
            logger.info("Stored %d evidence items for run %s.", len(rows), run_id)

        await asyncio.to_thread(_write)

    async def get_evidence(self, run_id: str) -> list[EvidenceItem]:
        """Retrieve all evidence items for a run."""
        schema_sql = _quote_ident(self.schema)

        def _read() -> list[EvidenceItem]:
            with self.conn.cursor() as cur:
                cur.execute(
                    f"""
SELECT evidence_id, run_id, chunk_id, document_id, page_idx, content_sha256,
       snippet, quality_score, quality_tier, source_type, created_at, is_global
FROM {schema_sql}.evidence_items
WHERE run_id = %s
ORDER BY created_at
                    """,
                    (run_id,),
                )
                return [_evidence_item_from_row(row) for row in cur.fetchall()]

        return await asyncio.to_thread(_read)

    async def find_similar_evidence(
        self, content_hash: str, threshold: float = 0.8
    ) -> list[EvidenceItem]:
        """Find evidence items across runs matching the given content hash.

        Because SHA-256 collisions are effectively impossible, *threshold* is
        accepted for API compatibility but exact hash matching is used.
        """
        schema_sql = _quote_ident(self.schema)

        def _read() -> list[EvidenceItem]:
            with self.conn.cursor() as cur:
                cur.execute(
                    f"""
SELECT evidence_id, run_id, chunk_id, document_id, page_idx, content_sha256,
       snippet, quality_score, quality_tier, source_type, created_at, is_global
FROM {schema_sql}.evidence_items
WHERE content_sha256 = %s
ORDER BY quality_score DESC, created_at DESC
                    """,
                    (content_hash,),
                )
                return [_evidence_item_from_row(row) for row in cur.fetchall()]

        return await asyncio.to_thread(_read)

    async def promote_to_global(self, run_id: str) -> int:
        """Promote run-level evidence to global evidence store.

        Returns the number of items promoted.
        """
        schema_sql = _quote_ident(self.schema)

        def _promote() -> int:
            count = 0
            promoted_ids: list[str] = []
            with index_transaction(self.conn):
                with self.conn.cursor() as cur:
                    cur.execute(
                        f"""
SELECT evidence_id, run_id, chunk_id, document_id, page_idx, content_sha256,
       snippet, quality_score, quality_tier, source_type
FROM {schema_sql}.evidence_items
WHERE run_id = %s AND is_global = FALSE
                        """,
                        (run_id,),
                    )
                    rows = cur.fetchall()
                    for row in rows:
                        (
                            evidence_id,
                            source_run_id,
                            chunk_id,
                            document_id,
                            page_idx,
                            content_sha256,
                            snippet,
                            quality_score,
                            quality_tier,
                            source_type,
                        ) = row
                        cur.execute(
                            f"""
INSERT INTO {schema_sql}.global_evidence
    (evidence_id, source_run_id, chunk_id, document_id, page_idx, content_sha256,
     snippet, quality_score, quality_tier, source_type)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (evidence_id) DO NOTHING
                            """,
                            (
                                evidence_id,
                                source_run_id,
                                chunk_id,
                                document_id,
                                page_idx,
                                content_sha256,
                                snippet,
                                quality_score,
                                quality_tier,
                                source_type,
                            ),
                        )
                        if cur.rowcount and cur.rowcount > 0:
                            count += 1
                            promoted_ids.append(evidence_id)
                    # Mark only actually-promoted items in the run-level table.
                    if promoted_ids:
                        cur.execute(
                            f"""
UPDATE {schema_sql}.evidence_items
SET is_global = TRUE
WHERE evidence_id = ANY(%s)
                            """,
                            (promoted_ids,),
                        )
            logger.info("Promoted %d evidence items from run %s to global store.", count, run_id)
            return count

        return await asyncio.to_thread(_promote)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _evidence_item_from_row(row: Any) -> EvidenceItem:
    (
        evidence_id,
        run_id,
        chunk_id,
        document_id,
        page_idx,
        content_sha256,
        snippet,
        quality_score,
        quality_tier,
        source_type,
        created_at,
        is_global,
    ) = row
    return EvidenceItem(
        evidence_id=str(evidence_id),
        run_id=str(run_id),
        chunk_id=str(chunk_id),
        document_id=str(document_id),
        page_idx=int(page_idx),
        content_sha256=str(content_sha256 or ""),
        snippet=str(snippet or ""),
        quality_score=float(quality_score or 0.0),
        quality_tier=str(quality_tier or "indirect_support"),
        source_type=str(source_type or "retrieval"),
        created_at=created_at if isinstance(created_at, datetime) else datetime.now(timezone.utc),
        is_global=bool(is_global),
    )


def compute_content_sha256(text: str) -> str:
    """Public helper: SHA-256 hex digest of arbitrary text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
