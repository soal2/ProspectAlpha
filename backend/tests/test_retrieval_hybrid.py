import yaml

from riskagent.retrieval import ChunkSearchResult
from riskagent.retrieval.hybrid import prospectus_hybrid_search


class FakePostgresIndexer:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def hybrid_search(self, **kwargs):
        self.calls.append(kwargs)
        return [
            ChunkSearchResult(
                chunk_id="block_2",
                document_id="doc_1",
                doc_key="doc_1",
                chunk_type="text",
                text="Related party transactions and redemption obligations are disclosed.",
                page_idx=2,
                source_file="prospectus.pdf",
                source_item_indices=[2],
                bbox_refs=[],
                merged_bboxes=[{"page_idx": 2, "bbox": [1, 1, 20, 20]}],
                image_path=None,
                mineru_img_path=None,
                table_html=None,
                metadata={"section_path": "关联交易"},
                score=0.95,
                vector_score=0.9,
                text_score=0.8,
            )
        ]


def test_hybrid_search_is_document_scoped_and_postgres_only():
    indexer = FakePostgresIndexer()

    results = prospectus_hybrid_search(
        query="redemption obligations",
        document_id="doc_1",
        limit=1,
        indexer=indexer,  # type: ignore[arg-type]
    )

    assert indexer.calls == [
        {
            "query": "redemption obligations",
            "document_id": "doc_1",
            "limit": 1,
            "vector_weight": 0.75,
        }
    ]
    assert results[0].chunk_id == "block_2"
    assert results[0].document_id == "doc_1"


class _UnavailablePostgresIndexer:
    def __init__(self, *args, **kwargs):
        from riskagent.retrieval.index import ChunkIndexError

        raise ChunkIndexError("PostgreSQL DATABASE_URL is required, got: sqlite")


def _write_local_document(tmp_path, monkeypatch) -> None:
    data = {
        "doc_local": {
            "document_id": "doc_local",
            "filename": "本地招股书.pdf",
            "content_type": "application/pdf",
            "file_sha256": "sha-local",
            "storage_uri": "file:///tmp/local.pdf",
            "file_size_bytes": 1,
            "page_count": 2,
            "text_chars": 100,
            "block_count": 2,
            "created_at": "2026-08-18T00:00:00Z",
            "blocks": [
                {
                    "block_id": "block_local_1",
                    "document_id": "doc_local",
                    "page_number": 1,
                    "section_path": "风险因素",
                    "block_type": "text",
                    "text": "经营活动现金流持续为负，现金消耗增加。",
                    "bbox": [1.0, 2.0, 3.0, 4.0],
                    "char_start": 0,
                    "char_end": 20,
                    "source_hash": "hash-1",
                },
                {
                    "block_id": "block_local_2",
                    "document_id": "doc_local",
                    "page_number": 2,
                    "section_path": "业务",
                    "block_type": "text",
                    "text": "公司主营业务为医疗器械研发。",
                    "bbox": [],
                    "char_start": 20,
                    "char_end": 40,
                    "source_hash": "hash-2",
                },
            ],
        }
    }
    docs_path = tmp_path / "documents.yaml"
    docs_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("DOCUMENTS_PATH", str(docs_path))
    monkeypatch.setenv("USE_DATABASE", "0")


def test_hybrid_search_falls_back_to_local_keyword_when_postgres_unavailable(tmp_path, monkeypatch):
    _write_local_document(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "riskagent.retrieval.hybrid.PostgresChunkIndexer", _UnavailablePostgresIndexer
    )

    results = prospectus_hybrid_search(query="现金流 现金消耗", document_id="doc_local", limit=2)

    assert results, "回落检索必须返回结果而不是让分析 run 失败"
    assert results[0].chunk_id == "block_local_1"
    assert results[0].metadata["degraded"] is True
    assert results[0].metadata["retrieval_mode"] == "local_keyword"
    assert results[0].page_idx == 1
    assert results[0].score > 0


def test_hybrid_search_local_fallback_ranks_relevant_block_first(tmp_path, monkeypatch):
    _write_local_document(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "riskagent.retrieval.hybrid.PostgresChunkIndexer", _UnavailablePostgresIndexer
    )

    results = prospectus_hybrid_search(query="医疗器械研发", document_id="doc_local", limit=2)

    assert results[0].chunk_id == "block_local_2"
