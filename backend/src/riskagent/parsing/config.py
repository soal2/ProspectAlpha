"""Configuration loading for PDF processing and chunking."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from riskagent.database import BACKEND_ROOT
from riskagent.parsing.chunker import ChunkRequest
from riskagent.parsing.pdf_parser import PdfProcessRequest


DEFAULT_RAG_CONFIG_PATH = BACKEND_ROOT / "configs" / "rag.yaml"


class RagConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class PdfProcessorSettings:
    name: str
    method: str
    backend: str
    lang: str
    device: str
    reuse_existing: bool
    image_analysis: bool


@dataclass(frozen=True)
class ChunkerSettings:
    name: str
    max_chars: int
    include_types: tuple[str, ...]
    skip_types: tuple[str, ...]
    merge_text: bool
    require_image_description: bool
    image_context_window_blocks: int


@dataclass(frozen=True)
class RagPipelineSettings:
    pdf_processor: PdfProcessorSettings
    chunker: ChunkerSettings


def load_rag_pipeline_settings(path: str | Path | None = None) -> RagPipelineSettings:
    config_path = Path(path or os.getenv("RAG_CONFIG_PATH") or DEFAULT_RAG_CONFIG_PATH)
    if not config_path.exists():
        raise RagConfigError(f"RAG config file does not exist: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise RagConfigError("RAG config must be a mapping.")

    pdf_processor = _load_pdf_processor_settings(raw)
    chunker = _load_chunker_settings(raw)
    return RagPipelineSettings(pdf_processor=pdf_processor, chunker=chunker)


def build_pdf_process_request(
    *,
    doc_key: str,
    document_id: str | None = None,
    pdf_path: Path | str,
    processed_root: Path | str,
    settings: PdfProcessorSettings,
) -> PdfProcessRequest:
    return PdfProcessRequest(
        doc_key=doc_key,
        document_id=document_id,
        pdf_path=pdf_path,
        processed_root=processed_root,
        method=settings.method,  # type: ignore[arg-type]
        backend=settings.backend,  # type: ignore[arg-type]
        lang=settings.lang,
        device=None if settings.device == "auto" else settings.device,
        reuse_existing=settings.reuse_existing,
        image_analysis=settings.image_analysis,
    )


def build_chunk_request(
    *,
    doc_key: str,
    document_id: str | None = None,
    content_list_path: Path | str,
    output_dir: Path | str | None,
    settings: ChunkerSettings,
) -> ChunkRequest:
    return ChunkRequest(
        doc_key=doc_key,
        document_id=document_id,
        content_list_path=content_list_path,
        output_dir=output_dir,
        max_chars=settings.max_chars,
        include_types=settings.include_types,
        skip_types=settings.skip_types,
        merge_text=settings.merge_text,
        require_image_description=settings.require_image_description,
        image_context_window_blocks=settings.image_context_window_blocks,
    )


def _load_pdf_processor_settings(raw: dict[str, Any]) -> PdfProcessorSettings:
    section = _mapping(raw, "pdf_processor")
    name = _string(section, "default", default="mineru")
    processor_config = _mapping(section, name)
    configured_device = os.getenv("MINERU_DEVICE") or _string(
        processor_config, "device", default="auto"
    )
    device = configured_device.strip().lower()
    if device == "cuda":
        cuda_index = os.getenv("MINERU_CUDA_DEVICE", "0").strip() or "0"
        device = f"cuda:{cuda_index}"
    if not (
        device in {"auto", "cpu", "mps"}
        or device.startswith("cuda:") and device.removeprefix("cuda:").isdigit()
    ):
        raise RagConfigError(
            "MinerU device must be auto, cpu, mps, cuda, or cuda:<index>"
        )
    return PdfProcessorSettings(
        name=name,
        method=_string(processor_config, "method", default="ocr"),
        backend=_string(processor_config, "backend", default="pipeline"),
        lang=_string(processor_config, "lang", default="chinese_cht"),
        device=device,
        reuse_existing=_bool(processor_config, "reuse_existing", default=True),
        image_analysis=_bool(processor_config, "image_analysis", default=True),
    )


def _load_chunker_settings(raw: dict[str, Any]) -> ChunkerSettings:
    section = _mapping(raw, "chunker")
    name = _string(section, "default", default="block")
    chunker_config = _mapping(section, name)
    max_chars = _int(chunker_config, "max_chars", default=1200)
    if max_chars <= 0:
        raise RagConfigError("chunker max_chars must be > 0")
    return ChunkerSettings(
        name=name,
        max_chars=max_chars,
        include_types=_string_tuple(chunker_config, "include_types", default=("text", "table", "image")),
        skip_types=_string_tuple(chunker_config, "skip_types", default=("header", "footer", "page_number")),
        merge_text=_bool(chunker_config, "merge_text", default=True),
        require_image_description=_bool(chunker_config, "require_image_description", default=False),
        image_context_window_blocks=_int(chunker_config, "image_context_window_blocks", default=3),
    )


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise RagConfigError(f"RAG config must define mapping: {key}")
    return value


def _string(raw: dict[str, Any], key: str, *, default: str) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise RagConfigError(f"RAG config value must be a non-empty string: {key}")
    return value.strip()


def _bool(raw: dict[str, Any], key: str, *, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise RagConfigError(f"RAG config value must be boolean: {key}")
    return value


def _int(raw: dict[str, Any], key: str, *, default: int) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int):
        raise RagConfigError(f"RAG config value must be integer: {key}")
    return value


def _string_tuple(raw: dict[str, Any], key: str, *, default: tuple[str, ...]) -> tuple[str, ...]:
    value = raw.get(key, list(default))
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise RagConfigError(f"RAG config value must be a list of strings: {key}")
    return tuple(value)
