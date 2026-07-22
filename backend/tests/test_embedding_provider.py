import os
import sys
from types import SimpleNamespace

import pytest

from riskagent.providers.embedding import (
    DashScopeEmbeddingClient,
    EmbeddingConfigError,
    create_embedding_client,
)


def test_dashscope_embedding_client_encodes_texts(monkeypatch):
    calls = []

    class FakeTextEmbedding:
        @staticmethod
        def call(*, model, input):
            calls.append({"model": model, "input": input})
            return {
                "status_code": 200,
                "request_id": "test-request",
                "code": "",
                "message": "",
                "output": {
                    "embeddings": [
                        {"text_index": 1, "embedding": [0.3, 0.4]},
                        {"text_index": 0, "embedding": [0.1, 0.2]},
                    ]
                },
                "usage": {"total_tokens": 2},
            }

    fake_dashscope = SimpleNamespace(TextEmbedding=FakeTextEmbedding, api_key="")
    monkeypatch.setitem(sys.modules, "dashscope", fake_dashscope)

    client = DashScopeEmbeddingClient(model="text-embedding-v4", api_key="test-key")

    assert client.encode(["first", "second"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert fake_dashscope.api_key == "test-key"
    assert calls == [{"model": "text-embedding-v4", "input": ["first", "second"]}]


def test_dashscope_embedding_client_reports_failed_response(monkeypatch):
    class FakeTextEmbedding:
        @staticmethod
        def call(*, model, input):
            return SimpleNamespace(status_code=400, code="InvalidInput", message="bad input")

    monkeypatch.setitem(sys.modules, "dashscope", SimpleNamespace(TextEmbedding=FakeTextEmbedding))
    client = DashScopeEmbeddingClient(model="text-embedding-v4")

    with pytest.raises(EmbeddingConfigError, match="DashScope embedding call failed"):
        client.encode(["bad"])


def test_create_embedding_client_rejects_unknown_provider():
    with pytest.raises(EmbeddingConfigError, match="Unsupported embedding provider"):
        create_embedding_client(provider="unknown", model="text-embedding-v4")


def test_dashscope_embedding_client_real_call():
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        pytest.skip("DASHSCOPE_API_KEY is required for the real DashScope embedding call.")

    input_texts = "衣服的质量杠杠的，很漂亮，不枉我等了这么久啊，喜欢，以后还来这里买"
    input_texts2 = "我就想知道你说的是啥"
    client = DashScopeEmbeddingClient(
        model=os.getenv("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v4"),
        api_key=api_key,
    )

    embeddings = client.encode([input_texts, input_texts2])

    assert len(embeddings) == 2
    assert embeddings[0]
    assert embeddings[1]
    assert len(embeddings[0]) == len(embeddings[1])
    assert all(isinstance(value, float) for value in embeddings[0])
    assert all(isinstance(value, float) for value in embeddings[1])
