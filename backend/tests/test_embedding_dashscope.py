"""Unit tests for the DashScope embedding client and default-client selection."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import openai
import pytest

from riskagent.providers.embedding import (
    MAX_TEXT_CHARS,
    DashScopeEmbeddingClient,
    EmbeddingConfigError,
    EmbeddingError,
    HashingEmbeddingClient,
    _prepare_text,
    get_default_embedding_client,
    reset_default_embedding_client,
)


class FakeEmbeddingsAPI:
    """Records calls and returns deterministic vectors, optionally failing first."""

    def __init__(self, dimensions: int = 4, failures: list[Exception] | None = None) -> None:
        self.dimensions = dimensions
        self.failures = list(failures or [])
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.failures:
            raise self.failures.pop(0)
        inputs = kwargs["input"]
        data = [
            SimpleNamespace(index=idx, embedding=[float(idx)] * self.dimensions)
            for idx in range(len(inputs))
        ]
        # Return out of order to verify index-based reordering.
        return SimpleNamespace(data=list(reversed(data)))


def _make_client(fake_api: FakeEmbeddingsAPI, **overrides) -> DashScopeEmbeddingClient:
    kwargs = {
        "base_url": "https://example.test/v1",
        "api_key": "sk-test",
        "model": "text-embedding-v4",
        "dimensions": fake_api.dimensions,
        "batch_size": 10,
    }
    kwargs.update(overrides)
    client = DashScopeEmbeddingClient(**kwargs)
    client._client = SimpleNamespace(embeddings=fake_api)
    return client


def _transient_error(cls):
    """Build an openai transient error instance without a real HTTP response."""
    exc = cls.__new__(cls)
    Exception.__init__(exc, "simulated transient failure")
    return exc


def test_config_error_when_missing_settings() -> None:
    with pytest.raises(EmbeddingConfigError):
        DashScopeEmbeddingClient(base_url="", api_key="sk-x", model="m")
    with pytest.raises(EmbeddingConfigError):
        DashScopeEmbeddingClient(base_url="https://x", api_key="", model="m")
    with pytest.raises(EmbeddingConfigError):
        DashScopeEmbeddingClient(base_url="https://x", api_key="sk-x", model="")


def test_embed_batch_splits_into_chunks_of_batch_size() -> None:
    fake = FakeEmbeddingsAPI()
    client = _make_client(fake, batch_size=10)
    texts = [f"text-{idx}" for idx in range(23)]
    vectors = client.embed_batch(texts)
    assert len(vectors) == 23
    assert [len(call["input"]) for call in fake.calls] == [10, 10, 3]


def test_embed_batch_reorders_by_index() -> None:
    fake = FakeEmbeddingsAPI()
    client = _make_client(fake)
    vectors = client.embed_batch(["a", "b", "c"])
    assert vectors[0] == [0.0] * fake.dimensions
    assert vectors[2] == [2.0] * fake.dimensions


def test_prepare_text_truncates_and_fills_blank() -> None:
    assert _prepare_text("") == "空白"
    assert _prepare_text("   \n ") == "空白"
    long_text = "证" * (MAX_TEXT_CHARS + 500)
    assert len(_prepare_text(long_text)) == MAX_TEXT_CHARS


def test_embed_batch_sends_prepared_texts() -> None:
    fake = FakeEmbeddingsAPI()
    client = _make_client(fake)
    client.embed_batch(["", "x" * (MAX_TEXT_CHARS + 10)])
    sent = fake.calls[0]["input"]
    assert sent[0] == "空白"
    assert len(sent[1]) == MAX_TEXT_CHARS


def test_transient_error_is_retried_then_succeeds() -> None:
    failures = [
        _transient_error(openai.APIConnectionError),
        _transient_error(openai.APITimeoutError),
    ]
    fake = FakeEmbeddingsAPI(failures=failures)
    client = _make_client(fake)
    vectors = client.embed_batch(["hello"])
    assert len(vectors) == 1
    assert len(fake.calls) == 3  # two failures + one success


def test_transient_errors_exhaust_retries_and_hard_fail() -> None:
    failures = [_transient_error(openai.APIConnectionError) for _ in range(3)]
    fake = FakeEmbeddingsAPI(failures=failures)
    client = _make_client(fake)
    with pytest.raises(EmbeddingError):
        client.embed_batch(["hello"])
    assert len(fake.calls) == 3


def test_non_transient_error_fails_immediately() -> None:
    fake = FakeEmbeddingsAPI(failures=[ValueError("arrearage")])
    client = _make_client(fake)
    with pytest.raises(EmbeddingError):
        client.embed_batch(["hello"])
    assert len(fake.calls) == 1


def test_vector_count_mismatch_raises() -> None:
    class ShortAPI(FakeEmbeddingsAPI):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(index=0, embedding=[0.0] * self.dimensions)]
            )

    client = _make_client(ShortAPI())
    with pytest.raises(EmbeddingError):
        client.embed_batch(["a", "b"])


def test_dimensions_forwarded_in_request_when_configured() -> None:
    fake = FakeEmbeddingsAPI(dimensions=1024)
    client = _make_client(fake, dimensions=1024)
    client.embed("hello")
    assert fake.calls[0]["dimensions"] == 1024
    assert client.provider_id == "text-embedding-v4-1024"


def test_dimensions_adjust_to_actual_response() -> None:
    fake = FakeEmbeddingsAPI(dimensions=768)
    client = _make_client(fake, dimensions=None)
    assert client.dimensions == 1024  # default before first call
    client.embed("hello")
    assert client.dimensions == 768
    assert client.provider_id == "text-embedding-v4-768"


def test_default_client_fails_without_embedding_key(monkeypatch) -> None:
    monkeypatch.setattr("riskagent.providers.embedding.load_dotenv", lambda: None)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    reset_default_embedding_client()
    try:
        with pytest.raises(EmbeddingConfigError, match="Embedding provider is required"):
            get_default_embedding_client()
    finally:
        reset_default_embedding_client()


def test_default_client_uses_dashscope_with_key(monkeypatch) -> None:
    monkeypatch.setattr("riskagent.providers.embedding.load_dotenv", lambda: None)
    monkeypatch.setenv("EMBEDDING_API_KEY", "sk-test")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-v4")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "1024")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "7")
    reset_default_embedding_client()
    try:
        with patch("openai.OpenAI") as mock_openai:
            client = get_default_embedding_client()
            assert isinstance(client, DashScopeEmbeddingClient)
            assert client.dimensions == 1024
            assert client.batch_size == 7
            assert get_default_embedding_client() is client  # singleton
            mock_openai.assert_called_once()
    finally:
        reset_default_embedding_client()


def test_hashing_client_is_deterministic_and_normalized() -> None:
    client = HashingEmbeddingClient()
    first = client.embed("盈利能力 下滑 风险")
    second = client.embed("盈利能力 下滑 风险")
    assert first == second
    norm = sum(value * value for value in first)
    assert norm == pytest.approx(1.0)
    batch = client.embed_batch(["a", "b"])
    assert len(batch) == 2
