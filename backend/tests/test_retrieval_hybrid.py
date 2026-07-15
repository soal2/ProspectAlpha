from dataclasses import dataclass
import sys
from types import SimpleNamespace

from riskagent.providers.vector_store import QdrantVectorStore, VectorMatch, VectorPoint
from riskagent.retrieval import ChunkSearchResult
from riskagent.retrieval.hybrid import HybridSearchIndex


@dataclass
class Block:
    block_id: str
    document_id: str
    page_number: int
    section_path: str
    text: str
    bbox: list[float]
    char_start: int = 0
    char_end: int = 0
    block_type: str = "text"
    source_hash: str = "hash"
    parser_version: str = "test"


def test_hybrid_index_compatibility_facade_uses_document_scoped_postgres(monkeypatch):
    calls: list[dict] = []

    def fake_postgres_search(**kwargs):
        calls.append(kwargs)
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

    monkeypatch.setattr(
        "riskagent.retrieval.hybrid.prospectus_hybrid_search", fake_postgres_search
    )
    index = HybridSearchIndex()

    blocks = [
        Block(
            block_id="block_1",
            document_id="doc_1",
            page_number=1,
            section_path="风险因素",
            text="The company has customer concentration risk.",
            bbox=[0, 0, 10, 10],
        ),
        Block(
            block_id="block_2",
            document_id="doc_1",
            page_number=2,
            section_path="关联交易",
            text="Related party transactions and redemption obligations are disclosed.",
            bbox=[1, 1, 20, 20],
        ),
    ]

    assert index.index_blocks(run_id="run_test", blocks=blocks) == 2
    results = index.search(run_id="run_test", query="redemption obligations", limit=1)
    assert calls == [
        {"query": "redemption obligations", "document_id": "doc_1", "limit": 1}
    ]
    assert results[0].block_id == "block_2"
    assert results[0].document_id == "doc_1"


def test_qdrant_vector_store_uses_query_points_api():
    calls = []

    class FakeClient:
        def get_collections(self):
            return SimpleNamespace(collections=[SimpleNamespace(name="test_collection")])

        def query_points(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                points=[
                    SimpleNamespace(
                        id="point-id",
                        score=0.77,
                        payload={"point_id": "block_1", "run_id": "run_test"},
                    )
                ]
            )

    store = QdrantVectorStore.__new__(QdrantVectorStore)
    store.client = FakeClient()
    store.vector_size = 8

    matches = store.search(
        collection="test_collection",
        vector=[0.1] * 8,
        run_id="run_test",
        limit=3,
    )

    assert calls[0]["query"] == [0.1] * 8
    assert calls[0]["limit"] == 3
    assert matches == [
        VectorMatch(
            point_id="block_1",
            score=0.77,
            payload={"point_id": "block_1", "run_id": "run_test"},
        )
    ]


def test_qdrant_vector_store_upserts_large_runs_in_bounded_batches():
    batches = []

    class FakeClient:
        def get_collections(self):
            return SimpleNamespace(collections=[SimpleNamespace(name="test_collection")])

        def upsert(self, **kwargs):
            batches.append(kwargs)

    store = QdrantVectorStore.__new__(QdrantVectorStore)
    store.client = FakeClient()
    store.vector_size = 8
    points = [
        VectorPoint(
            point_id=f"block_{index}",
            vector=[0.1] * 8,
            payload={"run_id": "run_test"},
        )
        for index in range(257)
    ]

    store.upsert(collection="test_collection", points=points)

    assert [len(call["points"]) for call in batches] == [128, 128, 1]
    assert {call["collection_name"] for call in batches} == {"test_collection"}


def test_qdrant_vector_store_bypasses_environment_proxy_for_loopback(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setitem(sys.modules, "qdrant_client", SimpleNamespace(QdrantClient=FakeClient))

    QdrantVectorStore(url="http://127.0.0.1:6333", vector_size=8)
    QdrantVectorStore(url="https://qdrant.example.com", vector_size=8)

    assert calls[0]["trust_env"] is False
    assert "trust_env" not in calls[1]
