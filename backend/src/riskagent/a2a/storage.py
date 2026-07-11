"""Small filesystem primitives shared by the A2A runtime stores."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import fcntl


DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AGENT_RUNTIME_ROOT = BACKEND_ROOT / "data" / "agent_runtime"


def resolve_agent_runtime_root(configured: str | Path | None = None) -> Path:
    """Resolve the mailbox/checkpoint root consistently across API and worker processes."""

    value = str(configured).strip() if configured is not None else ""
    value = value or os.getenv("AGENT_RUNTIME_DATA_DIR", "").strip()
    return Path(value) if value else DEFAULT_AGENT_RUNTIME_ROOT


def ensure_private_directory(path: Path) -> Path:
    path.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
    path.chmod(DIRECTORY_MODE)
    return path


def ensure_private_file(path: Path) -> Path:
    ensure_private_directory(path.parent)
    descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, FILE_MODE)
    os.close(descriptor)
    path.chmod(FILE_MODE)
    return path


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    ensure_private_directory(path.parent)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, FILE_MODE)
    try:
        os.chmod(path, FILE_MODE)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def append_json_line(path: Path, value: dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    encoded = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, FILE_MODE)
    try:
        written = 0
        while written < len(encoded):
            written += os.write(descriptor, encoded[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    path.chmod(FILE_MODE)


def atomic_write_json(path: Path, value: Any) -> None:
    ensure_private_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(FILE_MODE)
        _fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
