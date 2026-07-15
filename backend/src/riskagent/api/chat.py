"""General chat routes."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from riskagent.providers.llm import get_default_llm_client


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
