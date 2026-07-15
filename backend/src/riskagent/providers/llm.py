"""OpenAI-compatible LLM provider boundary."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from collections.abc import Callable
from typing import Any


class LLMConfigError(RuntimeError):
    pass


class LLMError(RuntimeError):
    """Base class for all LLM call-time failures (excluding config errors)."""


class LLMTransientError(LLMError):
    """Retryable failure: timeout, connection drop, 429 rate limit, 5xx server error."""


class LLMParseError(LLMError):
    """Non-retryable failure: the model returned a response that was not valid JSON
    or not a JSON object. Retrying is unlikely to help because the same prompt will
    likely produce the same shape of broken output.
    """


_TRANSIENT_OPENAI_EXC_NAMES = {
    "APITimeoutError",
    "APIConnectionError",
    "RateLimitError",
    "InternalServerError",
}


def _classify_openai_error(exc: BaseException) -> LLMError:
    """Map an openai SDK exception onto our LLM error hierarchy.

    The check is name-based so this still works if the openai package is not
    installed (e.g. in unit tests that only need the type symbol).
    """

    exc_name = type(exc).__name__
    message = f"{exc_name}: {exc}"
    if exc_name in _TRANSIENT_OPENAI_EXC_NAMES:
        err = LLMTransientError(message)
    else:
        err = LLMError(message)
    err.__cause__ = exc
    return err


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
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            raise _classify_openai_error(exc) from exc
        content = response.choices[0].message.content or ""
        try:
            parsed = json.loads(content)
        except ValueError as exc:
            raise LLMParseError(f"Failed to parse LLM JSON response: {content}") from exc
        if not isinstance(parsed, dict):
            raise LLMParseError(f"LLM JSON response must be an object: {content}")
        return parsed

    async def chat_json_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        on_delta: Callable[[str], None] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Collect an OpenAI-compatible JSON response while forwarding text deltas.

        The caller owns presentation and persistence.  In particular, it can
        broadcast deltas without turning every token into a database row.
        """

        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
                stream=True,
            )
        except Exception as exc:
            raise _classify_openai_error(exc) from exc
        chunks: list[str] = []
        try:
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if not delta:
                    continue
                chunks.append(delta)
                if on_delta:
                    on_delta(delta)
        except Exception as exc:
            raise _classify_openai_error(exc) from exc
        content = "".join(chunks)
        try:
            parsed = json.loads(content)
        except ValueError as exc:
            raise LLMParseError(f"Failed to parse LLM JSON response: {content}") from exc
        if not isinstance(parsed, dict):
            raise LLMParseError(f"LLM JSON response must be an object: {content}")
        return parsed, content

    async def chat_stream(self, messages: list[dict[str, str]], *, temperature: float = 0.4) -> AsyncIterator[str]:
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                stream=True,
            )
        except Exception as exc:
            raise _classify_openai_error(exc) from exc
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


def reset_default_llm_client() -> None:
    """Drop the cached default client. Intended for tests that swap env vars."""

    global _DEFAULT
    _DEFAULT = None
