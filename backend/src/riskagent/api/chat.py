"""Document chat routes."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from riskagent.documents import DocumentBlock, get_default_document_repository
from riskagent.providers.llm import get_default_llm_client


router = APIRouter(prefix="/documents", tags=["chat"])


class AskRequest(BaseModel):
    question: str
    top_k: int = 6


class AskCitation(BaseModel):
    block_id: str
    page_number: int
    section_path: str
    snippet: str


def _retrieve(blocks: list[DocumentBlock], query: str, top_k: int) -> list[DocumentBlock]:
    limit = max(1, top_k)
    terms = _terms(query)
    if not terms:
        return blocks[:limit]
    scored = []
    for block in blocks:
        text = block.text.lower()
        score = sum(text.count(term) for term in terms)
        if score > 0:
            scored.append((score, block))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [block for _, block in scored[:limit]] or blocks[:limit]


@router.post("/{document_id}/ask")
async def ask_document(document_id: str, payload: AskRequest) -> StreamingResponse:
    try:
        blocks = get_default_document_repository().get_blocks(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc

    retrieved = _retrieve(blocks, payload.question, payload.top_k)
    messages = _messages(payload.question, retrieved)

    async def event_source() -> AsyncIterator[str]:
        try:
            async for delta in get_default_llm_client().chat_stream(messages):
                yield f"data: {json.dumps({'type': 'delta', 'delta': delta}, ensure_ascii=False)}\n\n"
            citations = [
                AskCitation(
                    block_id=block.block_id,
                    page_number=block.page_number,
                    section_path=block.section_path,
                    snippet=block.text[:240],
                ).model_dump()
                for block in retrieved
            ]
            yield f"data: {json.dumps({'type': 'done', 'citations': citations, 'answer_complete': True}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _messages(question: str, blocks: list[DocumentBlock]) -> list[dict[str, str]]:
    snippets = "\n\n".join(
        f"[P.{block.page_number}] ({block.section_path}) {block.text}" for block in blocks
    )
    return [
        {
            "role": "system",
            "content": f"严格基于下方招股书片段回答；无法回答时直说；引用时用 [P.<页码>]。\n\n{snippets}",
        },
        {"role": "user", "content": question},
    ]


def _terms(query: str) -> list[str]:
    lowered = query.lower()
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", lowered)


general_router = APIRouter(tags=["chat"])


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


class ChatUnderstanding(BaseModel):
    company: str | None = None
    focus: list[str] = []
    material_hints: str | None = None
    clarifying_questions: list[str] = []


def _chat_system_prompt() -> str:
    return (
        "你是 ProspectAlpha 的 IPO 风险分析助手「沈北辰」。"
        "用户会描述他们要分析的标的、关注的风险或招股书线索。"
        "你的任务：(a) 用简洁的中文回应，必要时追问关键缺失信息（公司名称、招股书来源、关注重点）；"
        "(b) 不要编造数据；(c) 当用户描述足够清晰时，提示可以派发到 Agent 协同分析。"
    )


def _understanding_extraction_prompt(conversation: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "基于以下对话，抽取结构化的分析理解。返回 JSON 对象，字段：\n"
                "{\n"
                '  "company": string,  // 标的公司名；无法判断时为 null\n'
                '  "focus": string[],  // 关注的风险/议题列表\n'
                '  "material_hints": string,  // 招股书材料线索；无则为 null\n'
                '  "clarifying_questions": string[]  // 还需澄清的问题；无则空数组\n'
                "}\n"
                "只输出 JSON 对象，不要其他文字。"
            ),
        },
        {"role": "user", "content": conversation},
    ]


@general_router.post("/chat")
async def general_chat(payload: ChatRequest) -> StreamingResponse:
    """General-purpose LLM chat (no document required). Multi-turn. SSE."""
    client = get_default_llm_client()
    history_msgs = [{"role": h.role, "content": h.content} for h in payload.history]
    messages = [
        {"role": "system", "content": _chat_system_prompt()},
        *history_msgs,
        {"role": "user", "content": payload.message},
    ]

    async def event_source() -> AsyncIterator[str]:
        try:
            reply_parts: list[str] = []
            async for delta in client.chat_stream(messages):
                reply_parts.append(delta)
                yield f"data: {json.dumps({'type': 'delta', 'delta': delta}, ensure_ascii=False)}\n\n"
            reply = "".join(reply_parts)

            conversation = "\n\n".join(
                f"[{'用户' if h.role == 'user' else '沈北辰'}] {h.content}" for h in payload.history
            ) + f"\n\n[用户] {payload.message}\n\n[沈北辰] {reply}"

            understanding: ChatUnderstanding | None = None
            try:
                parsed = await client.chat_json(
                    _understanding_extraction_prompt(conversation), temperature=0.1
                )
                understanding = ChatUnderstanding(
                    company=parsed.get("company"),
                    focus=list(parsed.get("focus") or []),
                    material_hints=parsed.get("material_hints"),
                    clarifying_questions=list(parsed.get("clarifying_questions") or []),
                )
            except Exception:
                understanding = None

            done_payload: dict[str, object] = {
                "type": "done",
                "reply_complete": True,
                "reply": reply,
            }
            if understanding is not None:
                done_payload["understanding"] = understanding.model_dump()
            yield f"data: {json.dumps(done_payload, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
