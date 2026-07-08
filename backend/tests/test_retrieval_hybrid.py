from dataclasses import dataclass
from types import SimpleNamespace

from sqlalchemy.orm import sessionmaker

from riskagent.database import Base, make_engine
from riskagent.models import RetrievalIndexRow
from riskagent.providers.vector_store import QdrantVectorStore, VectorMatch, VectorPoint
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


class FakeVectorStore:
    def __init__(self) -> None:
        self.points: list[VectorPoint] = []
        self.search_calls: list[dict] = []

    def upsert(self, *, collection: str, points: list[VectorPoint]) -> None:
        self.points.extend(points)

    def search(
        self,
        *,
        collection: str,
        vector: list[float],
        run_id: str,
        limit: int,
    ) -> list[VectorMatch]:
        self.search_calls.append({"collection": collection, "run_id": run_id, "limit": limit})
        return [
            VectorMatch(
                point_id=self.points[-1].point_id,
                score=0.95,
                payload=self.points[-1].payload,
            )
        ]


def test_hybrid_index_persists_rows_and_uses_vector_store(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'retrieval.db'}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    vector_store = FakeVectorStore()
    index = HybridSearchIndex(
        session_factory=session_factory,
        vector_store=vector_store,
        collection_name="test_collection",
    )

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
    assert len(vector_store.points) == 2
    with session_factory() as session:
        assert session.query(RetrievalIndexRow).count() == 2

    results = index.search(run_id="run_test", query="redemption obligations", limit=1)
    assert vector_store.search_calls
    assert results[0].block_id == "block_2"
    assert results[0].vector_score >= 0.95


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
