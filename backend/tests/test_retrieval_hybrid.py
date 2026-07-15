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
