import sys
from types import SimpleNamespace

import pytest

from riskagent.providers.vllm import (
    DEFAULT_DASHSCOPE_BASE_URL,
    DashScopeVLLMClient,
    VLLMConfigError,
    VLLMImageDescriber,
    build_image_description_prompt,
    create_vllm_client,
    get_default_vllm_client,
)


def test_default_dashscope_endpoint_uses_native_api():
    assert DEFAULT_DASHSCOPE_BASE_URL == "https://dashscope.aliyuncs.com/api/v1"


def test_dashscope_vllm_client_generates_text(monkeypatch):
    calls = []

    class FakeMultiModalConversation:
        @staticmethod
        def call(**kwargs):
            calls.append(kwargs)
            return {
                "status_code": 200,
                "output": {
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {"text": "这张图显示了招股书页面。"},
                                ]
                            }
                        }
                    ]
                },
            }

    fake_dashscope = SimpleNamespace(
        MultiModalConversation=FakeMultiModalConversation,
        api_key="",
        base_http_api_url="",
    )
    monkeypatch.setitem(sys.modules, "dashscope", fake_dashscope)
    client = DashScopeVLLMClient(
        model="qwen3.7-plus",
        api_key="test-key",
        base_url="https://workspace.cn-beijing.maas.aliyuncs.com/api/v1",
    )

    result = client.generate(
        [
            {
                "role": "user",
                "content": [
                    {"image": "data:image/png;base64,abc"},
                    {"text": "请分析这张图"},
                ],
            }
        ],
        temperature=0.1,
        max_tokens=128,
    )

    assert result == "这张图显示了招股书页面。"
    assert fake_dashscope.api_key == "test-key"
    assert fake_dashscope.base_http_api_url == "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1"
    assert calls[0]["api_key"] == "test-key"
    assert calls[0]["model"] == "qwen3.7-plus"
    assert calls[0]["temperature"] == 0.1
    assert calls[0]["max_tokens"] == 128
    assert calls[0]["messages"] == [
        {
            "role": "user",
            "content": [
                {"image": "data:image/png;base64,abc"},
                {"text": "请分析这张图"},
            ],
        }
    ]


def test_dashscope_vllm_client_describe_image_builds_dashscope_message(monkeypatch, tmp_path):
    calls = []

    class FakeMultiModalConversation:
        @staticmethod
        def call(**kwargs):
            calls.append(kwargs)
            return {
                "status_code": 200,
                "output": {"choices": [{"message": {"content": [{"text": "图片描述"}]}}]},
            }

    monkeypatch.setitem(
        sys.modules,
        "dashscope",
        SimpleNamespace(MultiModalConversation=FakeMultiModalConversation, api_key=""),
    )
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    client = DashScopeVLLMClient(model="qwen3.7-plus", api_key="test-key")

    assert client.describe_image(image_path, "图中描绘的是什么景象?") == "图片描述"
    assert calls[0]["messages"] == [
        {
            "role": "user",
            "content": [
                {"image": "data:image/jpeg;base64,aW1hZ2U="},
                {"text": "图中描绘的是什么景象?"},
            ],
        }
    ]


def test_vllm_image_describer_includes_nearby_context(tmp_path):
    calls = []

    class FakeVLLMClient:
        def generate(self, messages, *, temperature=0.2, max_tokens=None):
            calls.append(
                {
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
            )
            return "结合上下文的图片描述"

    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    describer = VLLMImageDescriber(client=FakeVLLMClient(), max_tokens=256)

    result = describer.describe(
        image_path,
        {"page_idx": 3, "image_caption": ["研发管线图"]},
        {
            "page_idx": 3,
            "image_caption": ["研发管线图"],
            "image_footnote": [],
            "nearby_text_before": [{"source_item_index": 10, "text": "公司拥有两个临床阶段产品。"}],
            "nearby_text_after": [{"source_item_index": 12, "text": "图示展示适应症和研发进度。"}],
        },
    )

    assert result == "结合上下文的图片描述"
    prompt = calls[0]["messages"][0]["content"][1]["text"]
    assert "研发管线图" in prompt
    assert "公司拥有两个临床阶段产品" in prompt
    assert "图示展示适应症和研发进度" in prompt
    assert calls[0]["max_tokens"] == 256


def test_build_image_description_prompt_has_no_empty_context_noise():
    prompt = build_image_description_prompt(source_item={"page_idx": 0}, context={"page_idx": 0})
    assert "图片前附近文本:\n无" in prompt
    assert "不要编造" in prompt


def test_dashscope_vllm_client_reports_failed_response(monkeypatch):
    class FakeMultiModalConversation:
        @staticmethod
        def call(**kwargs):
            return {"status_code": 400, "code": "InvalidInput", "message": "bad input"}

    monkeypatch.setitem(
        sys.modules,
        "dashscope",
        SimpleNamespace(MultiModalConversation=FakeMultiModalConversation),
    )
    client = DashScopeVLLMClient(model="qwen3.7-plus")

    with pytest.raises(VLLMConfigError, match="DashScope VLLM call failed"):
        client.generate([{"role": "user", "content": "hello"}])


def test_create_vllm_client_rejects_unknown_provider():
    with pytest.raises(VLLMConfigError, match="Unsupported VLLM provider"):
        create_vllm_client(provider="unknown", model="qwen3.7-plus")


def test_get_default_vllm_client_uses_config_file(tmp_path, monkeypatch):
    calls = []

    class FakeMultiModalConversation:
        @staticmethod
        def call(**kwargs):
            calls.append(kwargs)
            return {
                "status_code": 200,
                "output": {"choices": [{"message": {"content": "ok"}}]},
            }

    monkeypatch.setitem(
        sys.modules,
        "dashscope",
        SimpleNamespace(MultiModalConversation=FakeMultiModalConversation, api_key=""),
    )
    monkeypatch.setenv("TEST_DASHSCOPE_KEY", "from-config")
    config_path = tmp_path / "providers.yaml"
    config_path.write_text(
        """
providers:
  vllm:
    default: qwen_vl
    models:
      qwen_vl:
        provider: dashscope
        model: qwen3.7-plus
        api_key_env: TEST_DASHSCOPE_KEY
""",
        encoding="utf-8",
    )

    client = get_default_vllm_client(config_path=config_path)

    assert client.generate([{"role": "user", "content": [{"text": "hello"}]}]) == "ok"
    assert calls[0]["api_key"] == "from-config"
    assert calls[0]["model"] == "qwen3.7-plus"
