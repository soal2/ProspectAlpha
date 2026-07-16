"""PDF processor factory and MinerU-backed implementation."""

from __future__ import annotations

import os
from importlib.util import find_spec
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from riskagent.database import BACKEND_ROOT


DEFAULT_PROCESSED_ROOT = BACKEND_ROOT / "data" / "processed"
ParseMethod = Literal["auto", "txt", "ocr"]
MinerUBackend = Literal[
    "pipeline",
    "vlm-transformers",
    "vlm-vllm-engine",
    "vlm-http-client",
    "vlm-mlx-engine",
]


@dataclass(frozen=True)
class PdfProcessRequest:
    doc_key: str
    pdf_path: Path | str
    document_id: str | None = None
    processed_root: Path | str = DEFAULT_PROCESSED_ROOT
    method: ParseMethod = "ocr"
    backend: MinerUBackend = "pipeline"
    lang: str = "chinese_cht"
    reuse_existing: bool = True
    start_page: int = 0
    end_page: int | None = None
    device: str | None = None
    image_analysis: bool = True


@dataclass(frozen=True)
class PdfProcessResult:
    document_id: str
    doc_key: str
    pdf_path: Path
    processed_root: Path
    output_dir: Path
    content_list_path: Path
    processor_name: str
    processor_version: str


class PdfProcessor(Protocol):
    name: str
    version: str

    def process(self, request: PdfProcessRequest) -> PdfProcessResult:
        ...


class MinerUPdfProcessor:
    name = "mineru"
    version = "mineru-v1"

    def process(self, request: PdfProcessRequest) -> PdfProcessResult:
        pdf_path = Path(request.pdf_path).expanduser().resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        if not pdf_path.is_file():
            raise ValueError(f"PDF path is not a file: {pdf_path}")

        processed_root = Path(request.processed_root).expanduser().resolve()
        document_id = request.document_id or request.doc_key
        mineru_file_name = document_id
        output_dir = processed_root / mineru_file_name / request.method
        content_list_path = output_dir / f"{mineru_file_name}_content_list.json"

        if not (request.reuse_existing and content_list_path.exists()):
            output_dir.mkdir(parents=True, exist_ok=True)
            _apply_mineru_env(output_dir)
            if request.device == "mps":
                os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
            _run_mineru(
                request=request,
                pdf_path=pdf_path,
                processed_root=processed_root,
                mineru_file_name=mineru_file_name,
            )

        if not content_list_path.exists():
            raise FileNotFoundError(
                f"MinerU content list not found after parsing: {content_list_path}"
            )

        return PdfProcessResult(
            document_id=document_id,
            doc_key=request.doc_key,
            pdf_path=pdf_path,
            processed_root=processed_root,
            output_dir=output_dir,
            content_list_path=content_list_path,
            processor_name=self.name,
            processor_version=self.version,
        )


class PdfProcessorFactory:
    def __init__(self) -> None:
        self._processors: dict[str, type[PdfProcessor]] = {
            MinerUPdfProcessor.name: MinerUPdfProcessor,
        }

    def register(self, name: str, processor: type[PdfProcessor]) -> None:
        self._processors[name] = processor

    def create(self, name: str = MinerUPdfProcessor.name) -> PdfProcessor:
        try:
            processor = self._processors[name]
        except KeyError as exc:
            known = ", ".join(sorted(self._processors))
            raise ValueError(f"Unknown PDF processor '{name}'. Known processors: {known}") from exc
        return processor()


def mineru_runtime_health() -> dict[str, object]:
    """Return a non-networking readiness check for the local MinerU runtime."""

    installed = find_spec("mineru") is not None
    model_source = os.getenv("MINERU_MODEL_SOURCE", "auto").strip().lower() or "auto"
    device = os.getenv("MINERU_DEVICE", "auto").strip().lower() or "auto"
    if device == "cuda":
        device = f"cuda:{os.getenv('MINERU_CUDA_DEVICE', '0').strip() or '0'}"
    modelscope_cache = os.getenv("MODELSCOPE_CACHE", "").strip()
    huggingface_cache = os.getenv("HF_HOME", "").strip()
    cache_roots = (
        [Path(modelscope_cache).expanduser()]
        if modelscope_cache
        else [
            Path("~/.cache/modelscope").expanduser(),
            Path("~/.cache/modelscope/hub").expanduser(),
            Path("~/.cache/modelscope/models").expanduser(),
        ]
    )
    cache_roots.extend(
        [Path(huggingface_cache).expanduser()]
        if huggingface_cache
        else [
            Path("~/.cache/huggingface").expanduser(),
            Path("~/.cache/huggingface/hub").expanduser(),
        ]
    )
    cache_patterns = ("**/*PDF-Extract-Kit*", "**/*pdf-extract-kit*")
    cache_present = any(
        root.exists()
        and any(any(root.glob(pattern)) for pattern in cache_patterns)
        for root in cache_roots
    )
    device_ready, device_message = _mineru_device_health(device)
    if not installed:
        status = "unavailable"
        message = "MinerU 未安装。"
    elif not device_ready:
        status = "device_unavailable"
        message = device_message
    elif cache_present:
        status = "ready"
        message = f"MinerU 已安装且检测到本地模型缓存；{device_message}"
    else:
        status = "model_download_required"
        message = (
            "MinerU 已安装，但首次解析前仍需从配置的模型源下载模型；"
            f"{device_message}"
        )
    return {
        "status": status,
        "installed": installed,
        "model_source": model_source,
        "device": device,
        "device_ready": device_ready,
        "model_cache_present": cache_present,
        "message": message,
    }


def _mineru_device_health(device: str) -> tuple[bool, str]:
    if device in {"auto", "cpu"}:
        return True, f"推理设备配置为 {device}。"
    try:
        import torch
    except ImportError:
        return False, "无法加载 PyTorch，不能验证 MinerU 推理设备。"
    if device == "mps":
        available = bool(torch.backends.mps.is_available())
        return available, (
            "MPS 设备可用。"
            if available
            else "当前 PyTorch 进程无法访问 MPS，请改用 cpu 或检查 PyTorch/macOS 环境。"
        )
    if device.startswith("cuda:"):
        index = int(device.split(":", 1)[1])
        available = bool(torch.cuda.is_available()) and index < torch.cuda.device_count()
        return available, (
            f"CUDA 设备 {index} 可用。"
            if available
            else f"CUDA 设备 {index} 不可用，请检查 NVIDIA 驱动、容器 GPU 映射和设备号。"
        )
    return False, f"不支持的 MinerU 推理设备：{device}。"


def process_pdf_with_mineru(
    *,
    doc_key: str,
    document_id: str | None = None,
    pdf_path: Path | str,
    processed_root: Path | str = DEFAULT_PROCESSED_ROOT,
    method: ParseMethod = "ocr",
    backend: MinerUBackend = "pipeline",
    lang: str = "chinese_cht",
    reuse_existing: bool = True,
    start_page: int = 0,
    end_page: int | None = None,
    device: str | None = None,
    image_analysis: bool = True,
) -> PdfProcessResult:
    return MinerUPdfProcessor().process(
        PdfProcessRequest(
            doc_key=doc_key,
            document_id=document_id,
            pdf_path=pdf_path,
            processed_root=processed_root,
            method=method,
            backend=backend,
            lang=lang,
            reuse_existing=reuse_existing,
            start_page=start_page,
            end_page=end_page,
            device=device,
            image_analysis=image_analysis,
        )
    )


def _run_mineru(
    *,
    request: PdfProcessRequest,
    pdf_path: Path,
    processed_root: Path,
    mineru_file_name: str,
) -> None:
    try:
        from mineru.cli.common import do_parse
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "MinerU is not installed. Install MinerU or call with reuse_existing=True "
            "when content_list.json already exists."
        ) from exc

    extra_kwargs = {}
    if request.device:
        extra_kwargs["device"] = request.device

    do_parse(
        output_dir=str(processed_root),
        pdf_file_names=[mineru_file_name],
        pdf_bytes_list=[pdf_path.read_bytes()],
        p_lang_list=[request.lang],
        backend=request.backend,
        parse_method=request.method,
        start_page_id=request.start_page,
        end_page_id=request.end_page,
        image_analysis=request.image_analysis,
        **extra_kwargs,
    )


def _apply_mineru_env(output_dir: Path) -> None:
    cache_dir = output_dir / ".cache"
    env_defaults = {
        "MPLCONFIGDIR": str(cache_dir / "matplotlib"),
        "YOLO_CONFIG_DIR": str(cache_dir / "ultralytics"),
        "XDG_CACHE_HOME": str(cache_dir / "xdg"),
    }
    for key, value in env_defaults.items():
        os.environ.setdefault(key, value)
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True)
