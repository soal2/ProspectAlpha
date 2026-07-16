"""Multimodal LLM provider boundary."""

from __future__ import annotations

import base64
import mimetypes
import os
from http import HTTPStatus
from pathlib import Path
from typing import Any, Protocol

from riskagent.providers.config import (
    DEFAULT_PROVIDER_CONFIG_PATH,
    ProviderConfigError,
    load_model_provider_config,
)

DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"


class VLLMConfigError(RuntimeError):
    pass


class VLLMClient(Protocol):
    def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ) -> str:
        ...


class OpenAIVLLMClient:
    """OpenAI-compatible multimodal chat client."""

    def __init__(self, *, model: str, api_key: str, base_url: str | None = None) -> None:
        self.model = model.strip()
        self.api_key = api_key.strip()
        self.base_url = (base_url or "").strip() or None
        if not self.model or not self.api_key:
            raise VLLMConfigError("OpenAI VLLM model and API key are required.")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise VLLMConfigError("openai package is required for OpenAI-compatible VLLM calls.") from exc
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(str(_get_field(item, "text", "")) for item in content)
        return ""


class DashScopeVLLMClient:
    """DashScope multimodal conversation client."""

    def __init__(self, *, model: str, api_key: str | None = None, base_url: str | None = None) -> None:
        self.model = model.strip()
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_DASHSCOPE_BASE_URL).strip()
        if not self.model:
            raise VLLMConfigError("DashScope VLLM model is required.")
        try:
            import dashscope
        except ImportError as exc:
            raise VLLMConfigError("dashscope package is required for DashScope VLLM calls.") from exc
        self.dashscope = dashscope
        if self.api_key:
            self.dashscope.api_key = self.api_key
        if self.base_url:
            self.dashscope.base_http_api_url = self.base_url

    def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if timeout is not None:
            kwargs["timeout"] = timeout
        response = self.dashscope.MultiModalConversation.call(**kwargs)
        status_code = _get_field(response, "status_code")
        if status_code != HTTPStatus.OK:
            code = _get_field(response, "code", "")
            message = _get_field(response, "message", "")
            raise VLLMConfigError(
                f"DashScope VLLM call failed: status={status_code}, code={code}, message={message}"
            )
        return _extract_dashscope_text(response)

    def describe_image(
        self,
        image: str | Path,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ) -> str:
        image_path = Path(image).expanduser().resolve()
        image_value = _encode_image_to_data_url(image_path)
        messages = [
            {
                "role": "user",
                "content": [
                    {"image": image_value},
                    {"text": prompt},
                ],
            }
        ]
        return self.generate(messages, temperature=temperature, max_tokens=max_tokens, timeout=timeout)


def _encode_image_to_data_url(image_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(image_path))
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/png"
    data = image_path.read_bytes()
    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


class VLLMImageDescriber:
    """Adapter used by parsing chunkers to describe MinerU image blocks."""

    def __init__(
        self,
        *,
        client: VLLMClient | None = None,
        prompt_version: str = "image-description-v1",
        temperature: float = 0.2,
        max_tokens: int = 512,
        request_timeout: int = 45,
    ) -> None:
        self.client = client or get_default_vllm_client()
        self.prompt_version = prompt_version
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.request_timeout = request_timeout

    def describe(
        self,
        image_path: Path,
        source_item: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> str:
        prompt = build_image_description_prompt(source_item=source_item, context=context or {})
        if hasattr(self.client, "describe_image"):
            return self.client.describe_image(  # type: ignore[attr-defined]
                image_path,
                prompt,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.request_timeout,
            )
        image_value = str(image_path.expanduser().resolve())
        return self.client.generate(
            [
                {
                    "role": "user",
                    "content": [
                        {"image": image_value},
                        {"text": prompt},
                    ],
                }
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )


def create_vllm_client(
    *,
    provider: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> VLLMClient:
    normalized = provider.strip().lower()
    if normalized == "dashscope":
        return DashScopeVLLMClient(model=model, api_key=api_key, base_url=base_url)
    if normalized == "openai":
        return OpenAIVLLMClient(model=model, api_key=api_key or "", base_url=base_url)
    raise VLLMConfigError(f"Unsupported VLLM provider: {provider}")


_DEFAULT: VLLMClient | None = None


def get_default_vllm_client(
    *,
    config_path: str | Path | None = None,
    selected_model_key: str | None = None,
) -> VLLMClient:
    if config_path is not None or selected_model_key is not None:
        config = load_model_provider_config(
            "vllm",
            path=config_path,
            selected_model_key=selected_model_key,
        )
        return create_vllm_client(
            provider=config.provider,
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
        )
    global _DEFAULT
    if _DEFAULT is None:
        try:
            config = load_model_provider_config("vllm")
            _DEFAULT = create_vllm_client(
                provider=config.provider,
                model=config.model,
                api_key=config.api_key,
                base_url=config.base_url,
            )
        except ProviderConfigError:
            if os.getenv("PROVIDER_CONFIG_PATH") or DEFAULT_PROVIDER_CONFIG_PATH.exists():
                raise
            _DEFAULT = create_vllm_client(
                provider=os.getenv("VLLM_PROVIDER", "dashscope"),
                model=os.getenv("DASHSCOPE_VLLM_MODEL", os.getenv("VLLM_MODEL", "qwen3.7-plus")),
                api_key=os.getenv("DASHSCOPE_API_KEY") or os.getenv("VLLM_API_KEY"),
                base_url=os.getenv("DASHSCOPE_BASE_HTTP_API_URL") or os.getenv("VLLM_BASE_URL"),
            )
    return _DEFAULT


def build_image_description_prompt(
    *,
    source_item: dict[str, Any],
    context: dict[str, Any],
) -> str:
    page_idx = context.get("page_idx", source_item.get("page_idx", ""))
    caption = _format_list(context.get("image_caption") or source_item.get("image_caption") or [])
    footnote = _format_list(context.get("image_footnote") or source_item.get("image_footnote") or [])
    before = _format_context_items(context.get("nearby_text_before") or [])
    after = _format_context_items(context.get("nearby_text_after") or [])
    return "\n".join(
        [
            "你正在解析招股书 PDF 的一张图片。请结合图片本身和附近文本上下文，生成用于检索和风控分析的中文描述。",
            "",
            f"页码 page_idx: {page_idx}",
            f"图片标题: {caption or '无'}",
            f"图片注释: {footnote or '无'}",
            "",
            "图片前附近文本:",
            before or "无",
            "",
            "图片后附近文本:",
            after or "无",
            "",
            "要求:",
            "1. 描述图片表达的核心信息。",
            "2. 保留公司、产品、金额、时间、风险相关信息。",
            "3. 如果图片与附近文本存在对应关系，请说明这种关系。",
            "4. 不要编造图片或上下文中没有的信息。",
        ]
    )


def _format_list(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            parts.append(item.strip())
    return "\n".join(parts)


def _format_context_items(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, dict):
            index = item.get("source_item_index", "")
            text = str(item.get("text", "")).strip()
            if text:
                parts.append(f"- [{index}] {text}")
    return "\n".join(parts)


def _extract_dashscope_text(response: Any) -> str:
    output = _get_field(response, "output")
    choices = _get_field(output, "choices") if output is not None else None
    if not isinstance(choices, list) or not choices:
        raise VLLMConfigError("DashScope VLLM response is missing output.choices.")
    message = _get_field(choices[0], "message")
    content = _get_field(message, "content") if message is not None else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(_get_field(item, "text", "")) for item in content)
    raise VLLMConfigError("DashScope VLLM response is missing message content.")


def _get_field(source: Any, field: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(field, default)
    return getattr(source, field, default)
