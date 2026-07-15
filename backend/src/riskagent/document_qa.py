"""Grounded question answering over PostgreSQL prospectus retrieval."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol

from riskagent.documents import DocumentRepository, get_default_document_repository
from riskagent.providers.llm import get_default_llm_client
from riskagent.retrieval import ChunkSearchResult, prospectus_hybrid_search


class DocumentQuestionError(RuntimeError):
    pass


class StreamingLLMClient(Protocol):
    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.4,
    ) -> AsyncIterator[str]: ...


@dataclass(frozen=True)
class DocumentCitation:
    block_id: str
    document_id: str
    page_number: int
    section_path: str
    snippet: str
    bbox_regions: list[dict[str, object]]
    bbox_coordinate_space: str


@dataclass(frozen=True)
class RetrievedQuestionContext:
    messages: list[dict[str, str]]
    citations: list[DocumentCitation]


class DocumentQuestionAnswerer:
    def __init__(
        self,
        *,
        repository: DocumentRepository | None = None,
        retriever: Callable[..., list[ChunkSearchResult]] = prospectus_hybrid_search,
        llm_client: StreamingLLMClient | None = None,
    ) -> None:
        self.repository = repository or get_default_document_repository()
        self.retriever = retriever
        self._llm_client = llm_client

    def retrieve(
        self,
        *,
        document_id: str,
        question: str,
        top_k: int,
    ) -> RetrievedQuestionContext:
        self.repository.prepare_for_retrieval(document_id)
        chunks = self.retriever(query=question, document_id=document_id, limit=top_k)
        if not chunks:
            raise DocumentQuestionError("当前文档没有检索到可用于回答的证据。")
        citations = [_citation(chunk) for chunk in chunks]
        context = "\n\n".join(
            f"[{index}] page={chunk.page_idx} section={_section(chunk)}\n{chunk.text[:4000]}"
            for index, chunk in enumerate(chunks, start=1)
        )
        return RetrievedQuestionContext(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是 ProspectAlpha 招股书问答助手。只能依据给定检索证据回答，"
                        "不得编造证据外事实。关键结论使用 [1]、[2] 标注证据编号；"
                        "证据不足时明确说明。使用简洁、准确的中文。"
                    ),
                },
                {"role": "user", "content": f"问题：{question}\n\n检索证据：\n{context}"},
            ],
            citations=citations,
        )

    async def answer_stream(
        self,
        context: RetrievedQuestionContext,
    ) -> AsyncIterator[str]:
        client = self._llm_client or get_default_llm_client()
        async for delta in client.chat_stream(context.messages, temperature=0.2):
            yield delta


def _citation(chunk: ChunkSearchResult) -> DocumentCitation:
    return DocumentCitation(
        block_id=chunk.chunk_id,
        document_id=chunk.document_id,
        page_number=chunk.page_idx,
        section_path=_section(chunk),
        snippet=chunk.text[:500],
        bbox_regions=_page_local_regions(chunk.merged_bboxes),
        bbox_coordinate_space=str(
            chunk.metadata.get("bbox_coordinate_space") or "pdf_points"
        ),
    )


def _section(chunk: ChunkSearchResult) -> str:
    return str(chunk.metadata.get("section_path") or chunk.chunk_type)


def _page_local_regions(items: list[dict[str, object]]) -> list[dict[str, object]]:
    by_page: dict[int, list[list[float]]] = {}
    for item in items:
        bbox = item.get("bbox") if isinstance(item, dict) else None
        page = int(item.get("page_idx", -1)) if isinstance(item, dict) else -1
        if page < 0 or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        by_page.setdefault(page, []).append([float(value) for value in bbox])
    return [
        {
            "page_number": page,
            "bbox": [
                min(box[0] for box in boxes),
                min(box[1] for box in boxes),
                max(box[2] for box in boxes),
                max(box[3] for box in boxes),
            ],
        }
        for page, boxes in sorted(by_page.items())
    ]
