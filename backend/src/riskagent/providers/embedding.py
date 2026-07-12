"""Embedding provider boundary for retrieval indexing."""

from __future__ import annotations

import os
from http import HTTPStatus
from pathlib import Path
from typing import Any, Protocol

from riskagent.providers.config import (
    DEFAULT_PROVIDER_CONFIG_PATH,
    ProviderConfigError,
    load_model_provider_config,
)


class EmbeddingConfigError(RuntimeError):
    pass


class EmbeddingClient(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed(self, text: str) -> list[float]:
        ...


class DashScopeEmbeddingClient:
    """DashScope text embedding client."""

    def __init__(self, *, model: str, api_key: str | None = None) -> None:
        self.model = model.strip()
        self.api_key = (api_key or "").strip()
        if not self.model:
            raise EmbeddingConfigError("DASHSCOPE_EMBEDDING_MODEL is required.")
        try:
            import dashscope
        except ImportError as exc:
            raise EmbeddingConfigError("dashscope package is required for DashScope embeddings.") from exc
        self.dashscope = dashscope
        if self.api_key:
            self.dashscope.api_key = self.api_key

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.dashscope.TextEmbedding.call(model=self.model, input=texts)
        status_code = _get_field(response, "status_code")
        if status_code != HTTPStatus.OK:
            code = _get_field(response, "code", "")
            message = _get_field(response, "message", "")
            raise EmbeddingConfigError(
                f"DashScope embedding call failed: status={status_code}, code={code}, message={message}"
            )
        embeddings = _extract_dashscope_embeddings(response)
        if len(embeddings) != len(texts):
            raise EmbeddingConfigError(
                f"DashScope returned {len(embeddings)} embeddings for {len(texts)} input texts."
            )
        return embeddings

    def embed(self, text: str) -> list[float]:
        return self.encode([text])[0]


class OpenAIEmbeddingClient:
    """OpenAI-compatible text embedding client."""

    def __init__(self, *, model: str, api_key: str, base_url: str | None = None) -> None:
        self.model = model.strip()
        self.api_key = api_key.strip()
        self.base_url = (base_url or "").strip() or None
        if not self.model or not self.api_key:
            raise EmbeddingConfigError("OPENAI_EMBEDDING_MODEL and OPENAI_API_KEY are required.")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise EmbeddingConfigError("openai package is required for OpenAI-compatible embeddings.") from exc
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(model=self.model, input=texts)
        embeddings = [list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)]
        if len(embeddings) != len(texts):
            raise EmbeddingConfigError(
                f"OpenAI returned {len(embeddings)} embeddings for {len(texts)} input texts."
            )
        return embeddings

    def embed(self, text: str) -> list[float]:
        return self.encode([text])[0]


def create_embedding_client(
    *,
    provider: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> EmbeddingClient:
    normalized = provider.strip().lower()
    if normalized == "dashscope":
        return DashScopeEmbeddingClient(model=model, api_key=api_key)
    if normalized == "openai":
        return OpenAIEmbeddingClient(model=model, api_key=api_key or "", base_url=base_url)
    raise EmbeddingConfigError(f"Unsupported embedding provider: {provider}")


_DEFAULT: EmbeddingClient | None = None


def get_default_embedding_client(
    *,
    config_path: str | Path | None = None,
    selected_model_key: str | None = None,
) -> EmbeddingClient:
    if config_path is not None or selected_model_key is not None:
        config = load_model_provider_config(
            "embedding",
            path=config_path,
            selected_model_key=selected_model_key,
        )
        return create_embedding_client(
            provider=config.provider,
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
        )
    global _DEFAULT
    if _DEFAULT is None:
        try:
            config = load_model_provider_config(
                "embedding",
            )
            _DEFAULT = create_embedding_client(
                provider=config.provider,
                model=config.model,
                api_key=config.api_key,
                base_url=config.base_url,
            )
        except ProviderConfigError:
            if os.getenv("PROVIDER_CONFIG_PATH") or DEFAULT_PROVIDER_CONFIG_PATH.exists():
                raise
            provider = os.getenv("EMBEDDING_PROVIDER", "dashscope").strip()
            if provider.lower() == "dashscope":
                _DEFAULT = create_embedding_client(
                    provider=provider,
                    model=os.getenv("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v4"),
                    api_key=os.getenv("DASHSCOPE_API_KEY"),
                )
            elif provider.lower() == "openai":
                _DEFAULT = create_embedding_client(
                    provider=provider,
                    model=os.getenv("OPENAI_EMBEDDING_MODEL", ""),
                    api_key=os.getenv("OPENAI_API_KEY"),
                    base_url=os.getenv("OPENAI_BASE_URL"),
                )
            else:
                _DEFAULT = create_embedding_client(provider=provider, model="")
    return _DEFAULT


def _extract_dashscope_embeddings(response: Any) -> list[list[float]]:
    output = _get_field(response, "output")
    if not isinstance(output, dict):
        raise EmbeddingConfigError("DashScope embedding response is missing output.")
    raw_embeddings = _get_field(output, "embeddings")
    if not isinstance(raw_embeddings, list):
        raise EmbeddingConfigError("DashScope embedding response is missing output.embeddings.")
    ordered = sorted(raw_embeddings, key=lambda item: _get_field(item, "text_index", 0))
    vectors: list[list[float]] = []
    for item in ordered:
        embedding = _get_field(item, "embedding")
        if not isinstance(embedding, list):
            raise EmbeddingConfigError("DashScope embedding item is missing embedding.")
        vectors.append([float(value) for value in embedding])
    return vectors


def _get_field(source: Any, field: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(field, default)
    return getattr(source, field, default)
