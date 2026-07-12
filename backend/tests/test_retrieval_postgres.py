from pathlib import Path

from riskagent.ingestion import ProspectusRegistration
from riskagent.parsing.chunker import BBoxRef, DocumentChunk, MergedBBox
from riskagent.retrieval.full_text import full_text_search
from riskagent.retrieval.hybrid import prospectus_hybrid_search
from riskagent.retrieval.index import ChunkIndexError, PostgresChunkIndexer, create_chunk_indexer, ensure_schema
from riskagent.retrieval.vector import vector_search


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.conn.calls.append({"sql": sql, "params": params})

    def fetchall(self):
        return self.conn.rows


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.rows = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class FakeEmbeddingClient:
    def encode(self, texts):
        return [self.embed(text) for text in texts]

    def embed(self, text):
        vector = [0.0, 0.0, 0.0]
        if "pipeline" in text.lower() or "管线" in text:
            vector[0] = 1.0
        if "risk" in text.lower() or "风险" in text:
            vector[1] = 1.0
        return vector


def _registration(tmp_path: Path) -> ProspectusRegistration:
    return ProspectusRegistration(
        document_id="doc_ysyy",
        doc_key="ysyy",
        original_filename="ysyy.pdf",
        raw_pdf_path=tmp_path / "raw" / "ysyy.pdf",
        content_type="application/pdf",
        file_sha256="hash",
        file_size_bytes=123,
        source_url=None,
        status="registered",
    )


def _chunk() -> DocumentChunk:
    return DocumentChunk(
        chunk_id="ysyy_chunk_000001",
        doc_key="ysyy",
        chunk_type="image",
        text="研发管线图展示产品进度和风险。",
        page_idx=2,
        source_file="backend/data/processed/ysyy/ocr/ysyy_content_list.json",
        source_item_indices=[10],
        bbox_refs=[BBoxRef(page_idx=2, bbox=[1, 2, 3, 4], source_item_index=10)],
        merged_bboxes=[MergedBBox(page_idx=2, bbox=[1, 2, 3, 4])],
        image_path="backend/data/processed/ysyy/ocr/images/image.jpg",
        mineru_img_path="images/image.jpg",
        metadata={"description_source": "vlm"},
    )


def test_ensure_schema_creates_pgvector_chunks_table():
    conn = FakeConnection()

    ensure_schema(schema="rag_test", conn=conn, embedding_dim=3, text_search_config="jiebacfg")

    sql = "\n".join(call["sql"] for call in conn.calls)
    assert 'CREATE TABLE IF NOT EXISTS "rag_test".prospectus_documents' in sql
    assert 'CREATE TABLE IF NOT EXISTS "rag_test".prospectus_chunks' in sql
    assert "embedding            VECTOR(3)" in sql
    assert "USING hnsw (embedding vector_cosine_ops)" in sql
    assert "to_tsvector('jiebacfg', NEW.text)" in sql


def test_postgres_chunk_indexer_inserts_project_doc_and_chunk_fields(tmp_path):
    conn = FakeConnection()
    indexer = PostgresChunkIndexer(
        schema="rag_test",
        conn=conn,
        embedding_client=FakeEmbeddingClient(),
        embedding_dim=3,
        ensure=False,
    )

    count = indexer.index_chunks(registration=_registration(tmp_path), chunks=[_chunk()])

    assert count == 1
    sql = "\n".join(call["sql"] for call in conn.calls)
    assert 'INSERT INTO "rag_test".prospectus_documents' in sql
    assert 'INSERT INTO "rag_test".prospectus_chunks' in sql
    assert "visibility" not in sql
    assert "tags" not in sql
    assert ".assets" not in sql
    chunk_call = [call for call in conn.calls if 'INSERT INTO "rag_test".prospectus_chunks' in call["sql"]][0]
    assert chunk_call["params"][0] == "ysyy_chunk_000001"
    assert chunk_call["params"][1] == "doc_ysyy"
    assert chunk_call["params"][2] == "ysyy"
    assert chunk_call["params"][5] == "[1.0,1.0,0.0]"
    assert chunk_call["params"][6] == 2
    assert '"description_source": "vlm"' in chunk_call["params"][-1]
    assert conn.commits == 1


def test_postgres_chunk_indexer_vector_full_text_and_hybrid_search_use_distinct_sql():
    conn = FakeConnection()
    conn.rows = [
        (
            "ysyy_chunk_000001",
            "doc_ysyy",
            "ysyy",
            "image",
            "研发管线图展示产品进度和风险。",
            2,
            "backend/data/processed/ysyy/ocr/ysyy_content_list.json",
            [10],
            [{"page_idx": 2, "bbox": [1, 2, 3, 4], "source_item_index": 10}],
            [{"page_idx": 2, "bbox": [1, 2, 3, 4]}],
            "backend/data/processed/ysyy/ocr/images/image.jpg",
            "images/image.jpg",
            None,
            {"page_idx": 2},
            0.91,
            0.2,
            0.7325,
        )
    ]
    indexer = PostgresChunkIndexer(
        schema="rag_test",
        conn=conn,
        embedding_client=FakeEmbeddingClient(),
        embedding_dim=3,
        ensure=False,
    )

    vector_results = indexer.vector_search(query="研发管线风险", document_id="doc_ysyy", limit=3)
    vector_call = conn.calls[-1]
    assert vector_results[0].chunk_id == "ysyy_chunk_000001"
    assert vector_results[0].doc_key == "ysyy"
    assert "AND document_id = %s" in vector_call["sql"]
    assert "AND doc_key = %s" not in vector_call["sql"]
    assert "ORDER BY embedding <=> %s::vector" in vector_call["sql"]
    assert "content_tsv @@" not in vector_call["sql"]
    assert vector_call["params"][0] == "[1.0,1.0,0.0]"
    assert vector_call["params"][2] == "doc_ysyy"
    assert vector_call["params"][-1] == 3

    full_text_results = indexer.full_text_search(query="研发管线风险", document_id="doc_ysyy", limit=3)
    full_text_call = conn.calls[-1]
    assert full_text_results[0].metadata["page_idx"] == 2
    assert "AND document_id = %s" in full_text_call["sql"]
    assert "content_tsv @@ plainto_tsquery" in full_text_call["sql"]
    assert "text ILIKE ANY" not in full_text_call["sql"]
    assert "embedding <=>" not in full_text_call["sql"]
    assert full_text_call["params"][6] == "doc_ysyy"

    hybrid_results = indexer.hybrid_search(query="研发管线风险", document_id="doc_ysyy", limit=3)
    hybrid_call = conn.calls[-1]
    assert hybrid_results[0].score == 0.7325
    assert "AND document_id = %s" in hybrid_call["sql"]
    assert "AND doc_key = %s" not in hybrid_call["sql"]
    assert "embedding <=> %s::vector" in hybrid_call["sql"]
    assert "content_tsv @@ plainto_tsquery" in hybrid_call["sql"]
    assert "text ILIKE ANY" not in hybrid_call["sql"]
    assert hybrid_call["params"][10] == "doc_ysyy"


def test_postgres_chunk_indexer_search_requires_document_id():
    indexer = PostgresChunkIndexer(
        schema="rag_test",
        conn=FakeConnection(),
        embedding_client=FakeEmbeddingClient(),
        embedding_dim=3,
        ensure=False,
    )

    try:
        indexer.full_text_search(query="研发管线风险", document_id="")
    except ChunkIndexError as exc:
        assert "document_id is required" in str(exc)
    else:
        raise AssertionError("expected ChunkIndexError")


def test_create_chunk_indexer_factory_returns_postgres_indexer():
    conn = FakeConnection()

    indexer = create_chunk_indexer(
        "postgres",
        schema="rag_test",
        conn=conn,
        embedding_client=FakeEmbeddingClient(),
        embedding_dim=3,
        ensure=False,
    )

    assert isinstance(indexer, PostgresChunkIndexer)


def test_vector_full_text_hybrid_wrapper_functions_call_matching_indexer_methods():
    class FakeIndexer:
        def __init__(self):
            self.calls = []

        def vector_search(self, **kwargs):
            self.calls.append(("vector", kwargs))
            return []

        def full_text_search(self, **kwargs):
            self.calls.append(("full_text", kwargs))
            return []

        def hybrid_search(self, **kwargs):
            self.calls.append(("hybrid", kwargs))
            return []

    indexer = FakeIndexer()

    vector_search(query="q", document_id="doc_ysyy", limit=2, indexer=indexer)
    full_text_search(query="q", document_id="doc_ysyy", limit=2, indexer=indexer)
    prospectus_hybrid_search(query="q", document_id="doc_ysyy", limit=2, indexer=indexer)

    assert [name for name, _ in indexer.calls] == ["vector", "full_text", "hybrid"]
    assert all(call["document_id"] == "doc_ysyy" for _, call in indexer.calls)
