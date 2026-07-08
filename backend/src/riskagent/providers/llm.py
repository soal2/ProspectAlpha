"""OpenAI-compatible LLM provider boundary."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any


class LLMConfigError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.strip()
        self.api_key = api_key.strip()
        self.model = model.strip()
        if not self.base_url or not self.api_key or not self.model:
            raise LLMConfigError("OPENAI_BASE_URL, OPENAI_API_KEY, and OPENAI_MODEL are required.")
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise LLMConfigError("openai package is required for LLM calls.") from exc
        self.client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)

    async def chat_json(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> dict[str, Any]:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or ""
        try:
            parsed = json.loads(content)
        except ValueError as exc:
            raise ValueError(f"Failed to parse LLM JSON response: {content}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"LLM JSON response must be an object: {content}")
        return parsed

    async def chat_stream(self, messages: list[dict[str, str]], *, temperature: float = 0.4) -> AsyncIterator[str]:
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


_DEFAULT: LLMClient | None = None


def get_default_llm_client() -> LLMClient:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = LLMClient(
            base_url=os.getenv("OPENAI_BASE_URL", "").strip(),
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            model=os.getenv("OPENAI_MODEL", "").strip(),
        )
    return _DEFAULT
