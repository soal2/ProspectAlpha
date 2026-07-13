"""Chunk indexing and PostgreSQL retrieval for prospectus RAG."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, Protocol

from sqlalchemy.engine import make_url

from riskagent.database import get_database_url
from riskagent.ingestion import ProspectusRegistration
from riskagent.parsing import DocumentChunk
from riskagent.providers.embedding import EmbeddingClient, get_default_embedding_client


DEFAULT_INDEX_BACKEND = "postgres"
DEFAULT_INDEX_SCHEMA = "rag"
DEFAULT_EMBEDDING_DIM = 1024
DEFAULT_EMBEDDING_BATCH_SIZE = 10


class ChunkIndexError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChunkSearchResult:
    chunk_id: str
    document_id: str
    doc_key: str
    chunk_type: str
    text: str
    page_idx: int
    source_file: str
    source_item_indices: list[int]
    bbox_refs: list[dict[str, Any]]
    merged_bboxes: list[dict[str, Any]]
    image_path: str | None
    mineru_img_path: str | None
    table_html: str | None
    metadata: dict[str, Any]
    vector_score: float = 0.0
    text_score: float = 0.0
    score: float = 0.0


class ChunkIndexer(Protocol):
    def index_chunks(
        self,
        *,
        registration: ProspectusRegistration,
        chunks: list[DocumentChunk],
        processed_path: str | None = None,
    ) -> int:
        ...

    def index_chunks_jsonl(
        self,
        *,
        registration: ProspectusRegistration,
        chunks_path: str | Path,
        processed_path: str | None = None,
    ) -> int:
        ...


def create_chunk_indexer(
    backend: str = DEFAULT_INDEX_BACKEND,
    **kwargs: Any,
) -> ChunkIndexer:
    normalized = backend.strip().lower()
    if normalized == "postgres":
        return PostgresChunkIndexer(**kwargs)
    raise ChunkIndexError(f"Unsupported chunk index backend: {backend}")


def connect_postgres(database_url: str | None = None):
    try:
        import psycopg
    except ImportError as exc:
        raise ChunkIndexError("psycopg is required for PostgreSQL chunk indexing.") from exc

    url = make_url(database_url or get_database_url())
    if not url.drivername.startswith("postgresql"):
        raise ChunkIndexError(f"PostgreSQL DATABASE_URL is required, got: {url.drivername}")
    return psycopg.connect(
        host=url.host,
        port=url.port,
        dbname=url.database,
        user=url.username,
        password=url.password,
    )


@contextmanager
def index_transaction(conn) -> Iterator[Any]:
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def ensure_extensions(conn) -> str:
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise ChunkIndexError(
            "PostgreSQL extension 'vector' is required for chunk indexing. "
            "Use a pgvector-enabled image such as pgvector/pgvector:pg16 and recreate the postgres service."
        ) from exc

    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_jieba")
            cur.execute(
                """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'jiebacfg') THEN
        CREATE TEXT SEARCH CONFIGURATION jiebacfg (PARSER = jieba);
        ALTER TEXT SEARCH CONFIGURATION jiebacfg
            ADD MAPPING FOR n, v, a, i, e, l WITH simple;
    END IF;
END$$
                """
            )
        conn.commit()
        return "jiebacfg"
    except Exception as exc:
        conn.rollback()
        raise ChunkIndexError(
            "PostgreSQL extension 'pg_jieba' and text search config 'jiebacfg' are required "
            "for Chinese full-text retrieval. Build/start the custom postgres image with pg_jieba "
            "instead of falling back to a simple tokenizer."
        ) from exc


def ensure_schema(
    *,
    schema: str,
    conn,
    embedding_dim: int = DEFAULT_EMBEDDING_DIM,
    text_search_config: str = "jiebacfg",
) -> None:
    schema_sql = _quote_ident(schema)
    tsv_config_sql = _quote_ts_config(text_search_config)
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_sql}")
        cur.execute(
            f"""
CREATE TABLE IF NOT EXISTS {schema_sql}.prospectus_documents (
    document_id        TEXT PRIMARY KEY,
    doc_key            TEXT NOT NULL UNIQUE,
    original_filename  TEXT NOT NULL,
    raw_pdf_path       TEXT NOT NULL,
    processed_path     TEXT,
    content_type       TEXT NOT NULL,
    file_sha256        TEXT NOT NULL,
    file_size_bytes    BIGINT NOT NULL,
    source_url         TEXT,
    created_at         TIMESTAMPTZ DEFAULT NOW()
)
            """
        )
        cur.execute(f"CREATE INDEX IF NOT EXISTS prospectus_documents_doc_key_idx ON {schema_sql}.prospectus_documents (doc_key)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS prospectus_documents_sha_idx ON {schema_sql}.prospectus_documents (file_sha256)")

        cur.execute(
            f"""
CREATE TABLE IF NOT EXISTS {schema_sql}.prospectus_chunks (
    chunk_id             TEXT PRIMARY KEY,
    document_id          TEXT NOT NULL REFERENCES {schema_sql}.prospectus_documents(document_id) ON DELETE CASCADE,
    doc_key              TEXT NOT NULL,
    chunk_type           TEXT NOT NULL,
    text                 TEXT NOT NULL,
    embedding            VECTOR({embedding_dim}) NOT NULL,
    content_tsv          TSVECTOR,
    page_idx             INT NOT NULL,
    source_file          TEXT NOT NULL,
    source_item_indices  JSONB NOT NULL,
    bbox_refs            JSONB NOT NULL,
    merged_bboxes        JSONB NOT NULL,
    image_path           TEXT,
    mineru_img_path      TEXT,
    table_html           TEXT,
    metadata             JSONB DEFAULT '{{}}',
    created_at           TIMESTAMPTZ DEFAULT NOW()
)
            """
        )
        cur.execute(f"CREATE INDEX IF NOT EXISTS prospectus_chunks_doc_key_idx ON {schema_sql}.prospectus_chunks (doc_key)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS prospectus_chunks_page_idx ON {schema_sql}.prospectus_chunks (doc_key, page_idx)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS prospectus_chunks_type_idx ON {schema_sql}.prospectus_chunks (chunk_type)")
        cur.execute(
            f"""
CREATE INDEX IF NOT EXISTS prospectus_chunks_embedding_hnsw
    ON {schema_sql}.prospectus_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
            """
        )
        cur.execute(f"CREATE INDEX IF NOT EXISTS prospectus_chunks_tsv_gin ON {schema_sql}.prospectus_chunks USING GIN (content_tsv)")
        cur.execute(
            f"""
CREATE OR REPLACE FUNCTION {schema_sql}.update_prospectus_chunk_tsv()
RETURNS TRIGGER AS $$
BEGIN
    NEW.content_tsv := to_tsvector('{tsv_config_sql}', NEW.text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
            """
        )
        cur.execute(f"DROP TRIGGER IF EXISTS prospectus_chunks_tsv_trigger ON {schema_sql}.prospectus_chunks")
        cur.execute(
            f"""
CREATE TRIGGER prospectus_chunks_tsv_trigger
BEFORE INSERT OR UPDATE ON {schema_sql}.prospectus_chunks
FOR EACH ROW EXECUTE FUNCTION {schema_sql}.update_prospectus_chunk_tsv()
            """
        )
    conn.commit()


class PostgresChunkIndexer:
    def __init__(
        self,
        *,
        schema: str = DEFAULT_INDEX_SCHEMA,
        conn=None,
        embedding_client: EmbeddingClient | None = None,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
        embedding_batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
        ensure: bool = True,
    ) -> None:
        self.schema = schema
        self.conn = conn or connect_postgres()
        self.owns_connection = conn is None
        self.embedding_client = embedding_client or get_default_embedding_client()
        self.embedding_dim = embedding_dim
        self.embedding_batch_size = embedding_batch_size
        self.text_search_config = "jiebacfg"
        if ensure:
            self.text_search_config = ensure_extensions(self.conn)
            ensure_schema(
                schema=self.schema,
                conn=self.conn,
                embedding_dim=self.embedding_dim,
                text_search_config=self.text_search_config,
            )

    def close(self) -> None:
        if self.owns_connection:
            self.conn.close()

    def index_chunks(
        self,
        *,
        registration: ProspectusRegistration,
        chunks: list[DocumentChunk],
        processed_path: str | None = None,
    ) -> int:
        usable = [chunk for chunk in chunks if chunk.text.strip()]
        vectors = self._encode_texts([chunk.text for chunk in usable])
        if len(vectors) != len(usable):
            raise ChunkIndexError(f"Embedding count mismatch: {len(vectors)} for {len(usable)} chunks.")
        for vector in vectors:
            if len(vector) != self.embedding_dim:
                raise ChunkIndexError(f"Embedding dimension mismatch: expected {self.embedding_dim}, got {len(vector)}.")
        embeddings = {chunk.chunk_id: vector for chunk, vector in zip(usable, vectors, strict=True)}
        with index_transaction(self.conn):
            self._upsert_document(registration=registration, processed_path=processed_path)
            self._upsert_chunks(registration=registration, chunks=usable, embeddings=embeddings)
        return len(usable)

    def _encode_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        batch_size = max(1, self.embedding_batch_size)
        for start in range(0, len(texts), batch_size):
            vectors.extend(self.embedding_client.encode(texts[start : start + batch_size]))
        return vectors

    def index_chunks_jsonl(
        self,
        *,
        registration: ProspectusRegistration,
        chunks_path: str | Path,
        processed_path: str | None = None,
    ) -> int:
        return self.index_chunks(
            registration=registration,
            chunks=read_chunks_jsonl(chunks_path),
            processed_path=processed_path,
        )

    def vector_search(
        self,
        *,
        query: str,
        document_id: str,
        limit: int = 5,
    ) -> list[ChunkSearchResult]:
        query_vector = self._query_vector(query)
        schema_sql = _quote_ident(self.schema)
        doc_filter, params = _document_id_filter(document_id)
        params = [
            _vector_literal(query_vector),
            _vector_literal(query_vector),
            *params,
            _vector_literal(query_vector),
            limit,
        ]
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
SELECT {_RESULT_COLUMNS},
       1 - (embedding <=> %s::vector) AS vector_score,
       0.0::float AS text_score,
       1 - (embedding <=> %s::vector) AS score
FROM {schema_sql}.prospectus_chunks
WHERE TRUE
  {doc_filter}
ORDER BY embedding <=> %s::vector
LIMIT %s
                """,
                params,
            )
            return [_search_result(row) for row in cur.fetchall()]

    def full_text_search(
        self,
        *,
        query: str,
        document_id: str,
        limit: int = 5,
    ) -> list[ChunkSearchResult]:
        schema_sql = _quote_ident(self.schema)
        doc_filter, doc_params = _document_id_filter(document_id)
        params: list[Any] = [
            self.text_search_config,
            query,
            self.text_search_config,
            query,
            self.text_search_config,
            query,
            *doc_params,
            limit,
        ]
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
SELECT {_RESULT_COLUMNS},
       0.0::float AS vector_score,
       ts_rank_cd(content_tsv, plainto_tsquery(%s::regconfig, %s)) AS text_score,
       ts_rank_cd(content_tsv, plainto_tsquery(%s::regconfig, %s)) AS score
FROM {schema_sql}.prospectus_chunks
WHERE content_tsv @@ plainto_tsquery(%s::regconfig, %s)
  {doc_filter}
ORDER BY score DESC
LIMIT %s
                """,
                params,
            )
            return [_search_result(row) for row in cur.fetchall()]

    def hybrid_search(
        self,
        *,
        query: str,
        document_id: str,
        limit: int = 5,
        vector_weight: float = 0.75,
    ) -> list[ChunkSearchResult]:
        query_vector = self._query_vector(query)
        text_weight = 1.0 - vector_weight
        schema_sql = _quote_ident(self.schema)
        doc_filter, doc_params = _document_id_filter(document_id)
        params: list[Any] = [
            _vector_literal(query_vector),
            self.text_search_config,
            query,
            vector_weight,
            _vector_literal(query_vector),
            text_weight,
            self.text_search_config,
            query,
            self.text_search_config,
            query,
            *doc_params,
            limit,
        ]
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
SELECT {_RESULT_COLUMNS},
       1 - (embedding <=> %s::vector) AS vector_score,
       ts_rank_cd(content_tsv, plainto_tsquery(%s::regconfig, %s)) AS text_score,
       (%s * (1 - (embedding <=> %s::vector))
        + %s * ts_rank_cd(content_tsv, plainto_tsquery(%s::regconfig, %s))) AS score
FROM {schema_sql}.prospectus_chunks
WHERE (
    content_tsv @@ plainto_tsquery(%s::regconfig, %s)
    OR embedding IS NOT NULL
)
  {doc_filter}
ORDER BY score DESC
LIMIT %s
                """,
                params,
            )
            return [_search_result(row) for row in cur.fetchall()]

    def search(
        self,
        *,
        query: str,
        mode: Literal["vector", "full_text", "hybrid"] = "hybrid",
        document_id: str,
        limit: int = 5,
    ) -> list[ChunkSearchResult]:
        if mode == "vector":
            return self.vector_search(query=query, document_id=document_id, limit=limit)
        if mode == "full_text":
            return self.full_text_search(query=query, document_id=document_id, limit=limit)
        if mode == "hybrid":
            return self.hybrid_search(query=query, document_id=document_id, limit=limit)
        raise ChunkIndexError(f"Unsupported search mode: {mode}")

    def _query_vector(self, query: str) -> list[float]:
        query_vector = self.embedding_client.embed(query)
        if len(query_vector) != self.embedding_dim:
            raise ChunkIndexError(f"Embedding dimension mismatch: expected {self.embedding_dim}, got {len(query_vector)}.")
        return query_vector

    def _upsert_document(
        self,
        *,
        registration: ProspectusRegistration,
        processed_path: str | None,
    ) -> None:
        schema_sql = _quote_ident(self.schema)
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
INSERT INTO {schema_sql}.prospectus_documents
    (document_id, doc_key, original_filename, raw_pdf_path, processed_path,
     content_type, file_sha256, file_size_bytes, source_url)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (document_id) DO UPDATE SET
    doc_key = EXCLUDED.doc_key,
    original_filename = EXCLUDED.original_filename,
    raw_pdf_path = EXCLUDED.raw_pdf_path,
    processed_path = EXCLUDED.processed_path,
    content_type = EXCLUDED.content_type,
    file_sha256 = EXCLUDED.file_sha256,
    file_size_bytes = EXCLUDED.file_size_bytes,
    source_url = EXCLUDED.source_url
                """,
                (
                    registration.document_id,
                    registration.doc_key,
                    registration.original_filename,
                    str(registration.raw_pdf_path),
                    processed_path,
                    registration.content_type,
                    registration.file_sha256,
                    registration.file_size_bytes,
                    registration.source_url,
                ),
            )

    def _upsert_chunks(
        self,
        *,
        registration: ProspectusRegistration,
        chunks: list[DocumentChunk],
        embeddings: dict[str, list[float]],
    ) -> None:
        schema_sql = _quote_ident(self.schema)
        with self.conn.cursor() as cur:
            for chunk in chunks:
                embedding = embeddings.get(chunk.chunk_id)
                if embedding is None:
                    raise ChunkIndexError(f"Missing embedding for chunk_id: {chunk.chunk_id}")
                cur.execute(
                    f"""
INSERT INTO {schema_sql}.prospectus_chunks
    (chunk_id, document_id, doc_key, chunk_type, text, embedding, page_idx,
     source_file, source_item_indices, bbox_refs, merged_bboxes, image_path,
     mineru_img_path, table_html, metadata)
VALUES (%s, %s, %s, %s, %s, %s::vector, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s::jsonb)
ON CONFLICT (chunk_id) DO UPDATE SET
    document_id = EXCLUDED.document_id,
    doc_key = EXCLUDED.doc_key,
    chunk_type = EXCLUDED.chunk_type,
    text = EXCLUDED.text,
    embedding = EXCLUDED.embedding,
    page_idx = EXCLUDED.page_idx,
    source_file = EXCLUDED.source_file,
    source_item_indices = EXCLUDED.source_item_indices,
    bbox_refs = EXCLUDED.bbox_refs,
    merged_bboxes = EXCLUDED.merged_bboxes,
    image_path = EXCLUDED.image_path,
    mineru_img_path = EXCLUDED.mineru_img_path,
    table_html = EXCLUDED.table_html,
    metadata = EXCLUDED.metadata
                    """,
                    (
                        chunk.chunk_id,
                        registration.document_id,
                        chunk.doc_key,
                        chunk.chunk_type,
                        chunk.text,
                        _vector_literal(embedding),
                        chunk.page_idx,
                        chunk.source_file,
                        json.dumps(chunk.source_item_indices, ensure_ascii=False),
                        json.dumps([item.__dict__ for item in chunk.bbox_refs], ensure_ascii=False),
                        json.dumps([item.__dict__ for item in chunk.merged_bboxes], ensure_ascii=False),
                        chunk.image_path,
                        chunk.mineru_img_path,
                        chunk.table_html,
                        json.dumps(chunk.metadata or {}, ensure_ascii=False),
                    ),
                )


def read_chunks_jsonl(path: str | Path) -> list[DocumentChunk]:
    chunks: list[DocumentChunk] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        chunks.append(_chunk_from_dict(json.loads(line)))
    return chunks


_RESULT_COLUMNS = """
chunk_id,
document_id,
doc_key,
chunk_type,
text,
page_idx,
source_file,
source_item_indices,
bbox_refs,
merged_bboxes,
image_path,
mineru_img_path,
table_html,
metadata
"""


def _chunk_from_dict(payload: dict[str, Any]) -> DocumentChunk:
    from riskagent.parsing.chunker import BBoxRef, MergedBBox

    return DocumentChunk(
        chunk_id=payload["chunk_id"],
        document_id=payload.get("document_id") or payload["doc_key"],
        doc_key=payload["doc_key"],
        chunk_type=payload["chunk_type"],
        text=payload["text"],
        page_idx=int(payload["page_idx"]),
        source_file=payload["source_file"],
        source_item_indices=[int(value) for value in payload.get("source_item_indices", [])],
        bbox_refs=[
            BBoxRef(
                page_idx=int(item["page_idx"]),
                bbox=[float(value) for value in item["bbox"]],
                source_item_index=int(item["source_item_index"]),
            )
            for item in payload.get("bbox_refs", [])
        ],
        merged_bboxes=[
            MergedBBox(page_idx=int(item["page_idx"]), bbox=[float(value) for value in item["bbox"]])
            for item in payload.get("merged_bboxes", [])
        ],
        image_path=payload.get("image_path"),
        mineru_img_path=payload.get("mineru_img_path"),
        table_html=payload.get("table_html"),
        metadata=payload.get("metadata") or {},
    )


def _search_result(row: Any) -> ChunkSearchResult:
    (
        chunk_id,
        document_id,
        doc_key,
        chunk_type,
        text,
        page_idx,
        source_file,
        source_item_indices,
        bbox_refs,
        merged_bboxes,
        image_path,
        mineru_img_path,
        table_html,
        metadata,
        vector_score,
        text_score,
        score,
    ) = row
    return ChunkSearchResult(
        chunk_id=str(chunk_id),
        document_id=str(document_id),
        doc_key=str(doc_key),
        chunk_type=str(chunk_type),
        text=str(text),
        page_idx=int(page_idx),
        source_file=str(source_file),
        source_item_indices=list(source_item_indices or []),
        bbox_refs=list(bbox_refs or []),
        merged_bboxes=list(merged_bboxes or []),
        image_path=image_path,
        mineru_img_path=mineru_img_path,
        table_html=table_html,
        metadata=metadata if isinstance(metadata, dict) else {},
        vector_score=float(vector_score or 0.0),
        text_score=float(text_score or 0.0),
        score=float(score or 0.0),
    )


def _document_id_filter(document_id: str) -> tuple[str, list[Any]]:
    if not document_id or not document_id.strip():
        raise ChunkIndexError("document_id is required for prospectus retrieval.")
    return "AND document_id = %s", [document_id]


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in vector) + "]"


def _quote_ident(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ChunkIndexError(f"Unsafe SQL identifier: {value}")
    return '"' + value.replace('"', '""') + '"'


def _quote_ts_config(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ChunkIndexError(f"Unsafe text search config: {value}")
    return value
