from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from riskagent.api.main import create_app
from riskagent.document_qa import DocumentQuestionAnswerer, RetrievedQuestionContext
from riskagent.retrieval import ChunkSearchResult


class FakeRepository:
    def __init__(self) -> None:
        self.prepared: list[str] = []

    def prepare_for_retrieval(self, document_id: str):
        self.prepared.append(document_id)


class FakeLLM:
    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.4,
    ) -> AsyncIterator[str]:
        assert "[1]" in messages[-1]["content"]
        assert temperature == 0.2
        yield "根据证据"
        yield "，存在风险。[1]"


def _retriever(**kwargs) -> list[ChunkSearchResult]:
    assert kwargs["document_id"] == "doc_qa"
    return [
        ChunkSearchResult(
            chunk_id="chunk_1",
            document_id="doc_qa",
            doc_key="doc_qa",
            chunk_type="text",
            text="公司披露经营活动现金流持续为负。",
            page_idx=3,
            source_file="prospectus.pdf",
            source_item_indices=[1],
            bbox_refs=[],
            merged_bboxes=[
                {"page_idx": 3, "bbox": [10, 20, 30, 40]},
                {"page_idx": 3, "bbox": [5, 25, 35, 45]},
                {"page_idx": 4, "bbox": [1, 2, 3, 4]},
            ],
            image_path=None,
            mineru_img_path=None,
            table_html=None,
            metadata={"section_path": "风险因素"},
            score=0.91,
        )
    ]


def test_document_qa_uses_document_scoped_postgres_retrieval_and_page_local_bbox():
    repository = FakeRepository()
    answerer = DocumentQuestionAnswerer(
        repository=repository,  # type: ignore[arg-type]
        retriever=_retriever,
        llm_client=FakeLLM(),
    )

    context = answerer.retrieve(document_id="doc_qa", question="现金流如何？", top_k=6)

    assert repository.prepared == ["doc_qa"]
    assert context.citations[0].page_number == 3
    assert context.citations[0].bbox_regions == [
        {"page_number": 3, "bbox": [5.0, 20.0, 35.0, 45.0]},
        {"page_number": 4, "bbox": [1.0, 2.0, 3.0, 4.0]},
    ]


def test_document_ask_stream_contract(monkeypatch):
    class FakeAnswerer:
        def retrieve(self, **_kwargs):
            return RetrievedQuestionContext(messages=[], citations=[])

        async def answer_stream(self, _context):
            yield "回答"

    monkeypatch.setattr("riskagent.api.documents._answerer", lambda: FakeAnswerer())
    client = TestClient(create_app())

    response = client.post(
        "/documents/doc_qa/ask",
        json={"question": "核心风险是什么？", "top_k": 4},
    )

    assert response.status_code == 200
    assert '"type": "delta"' in response.text
    assert '"type": "done"' in response.text


def test_document_ask_rejects_invalid_top_k(monkeypatch):
    monkeypatch.setattr("riskagent.api.documents._answerer", lambda: object())
    client = TestClient(create_app())

    response = client.post(
        "/documents/doc_qa/ask",
        json={"question": "问题", "top_k": 20},
    )

    assert response.status_code == 422
