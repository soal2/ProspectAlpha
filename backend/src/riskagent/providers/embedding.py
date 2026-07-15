"""Embedding provider boundary for retrieval indexing."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from typing import Protocol

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# DashScope text-embedding-v3/v4 accept up to 8192 tokens per input; keep a
# conservative character budget so CJK-heavy blocks never exceed the limit.
MAX_TEXT_CHARS = 6000


class EmbeddingConfigError(RuntimeError):
    """Raised when embedding provider configuration is incomplete."""


class EmbeddingError(RuntimeError):
    """Raised when a real embedding call ultimately fails; never silently degraded."""


class EmbeddingClient(Protocol):
    dimensions: int
    provider_id: str

    def embed(self, text: str) -> list[float]:
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...


class HashingEmbeddingClient:
    """Deterministic local embedding used when no external provider is configured."""

    provider_id = "hashing-64"

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[idx] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


def _is_transient(exc: BaseException) -> bool:
    import openai

    return isinstance(
        exc,
        (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
        ),
    )


class DashScopeEmbeddingClient:
    """OpenAI-compatible embedding client (Aliyun DashScope by default).

    Hard-fails with :class:`EmbeddingError` after retries — a run must never
    silently continue on degraded vectors.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int | None = None,
        batch_size: int = 10,
    ) -> None:
        if not base_url or not api_key or not model:
            raise EmbeddingConfigError(
                "Embedding provider requires EMBEDDING_BASE_URL, EMBEDDING_API_KEY and EMBEDDING_MODEL."
            )
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.dimensions = int(dimensions) if dimensions else 1024
        self._request_dimensions = int(dimensions) if dimensions else None
        self.batch_size = max(1, int(batch_size))
        self.provider_id = f"{model}-{self.dimensions}"

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        prepared = [_prepare_text(text) for text in texts]
        vectors: list[list[float]] = []
        for start in range(0, len(prepared), self.batch_size):
            chunk = prepared[start : start + self.batch_size]
            vectors.extend(self._embed_chunk(chunk))
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"Embedding provider returned {len(vectors)} vectors for {len(texts)} inputs."
            )
        return vectors

    @retry(
        retry=retry_if_exception(_is_transient),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _call_api(self, chunk: list[str]):
        kwargs: dict = {"model": self.model, "input": chunk}
        if self._request_dimensions:
            kwargs["dimensions"] = self._request_dimensions
        return self._client.embeddings.create(**kwargs)

    def _embed_chunk(self, chunk: list[str]) -> list[list[float]]:
        try:
            response = self._call_api(chunk)
        except Exception as exc:  # noqa: BLE001 - normalized to EmbeddingError
            raise EmbeddingError(f"Embedding call failed ({self.model}): {exc}") from exc
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = [list(item.embedding) for item in ordered]
        for vector in vectors:
            if len(vector) != self.dimensions:
                self.dimensions = len(vector)
                self.provider_id = f"{self.model}-{self.dimensions}"
        return vectors


def _prepare_text(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        # DashScope rejects empty strings; use a neutral placeholder token.
        return "空白"
    return cleaned[:MAX_TEXT_CHARS]


_default_client: EmbeddingClient | None = None


def get_default_embedding_client() -> EmbeddingClient:
    global _default_client
    if _default_client is not None:
        return _default_client
    api_key = os.getenv("EMBEDDING_API_KEY", "").strip()
    if api_key:
        _default_client = DashScopeEmbeddingClient(
            base_url=os.getenv(
                "EMBEDDING_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            api_key=api_key,
            model=os.getenv("EMBEDDING_MODEL", "text-embedding-v4"),
            dimensions=int(os.getenv("EMBEDDING_DIMENSIONS", "1024") or 0) or None,
            batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "10")),
        )
    else:
        logger.warning(
            "EMBEDDING_API_KEY is not set; falling back to the deterministic hashing "
            "embedder. Retrieval quality will be poor — configure a real provider."
        )
        _default_client = HashingEmbeddingClient()
    return _default_client


def reset_default_embedding_client() -> None:
    global _default_client
    _default_client = None


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    latin = re.findall(r"[a-z0-9]+", lowered)
    cjk = re.findall(r"[一-鿿]{2,}", lowered)
    return latin + cjk


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    return sum(left[idx] * right[idx] for idx in range(size))
