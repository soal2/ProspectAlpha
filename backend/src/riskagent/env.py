"""Small .env loader for local backend configuration."""

from __future__ import annotations

import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = BACKEND_ROOT / ".env"
_LOADED = False


def load_dotenv(path: str | Path | None = None) -> None:
    """Load KEY=VALUE pairs from .env without overriding real environment vars."""
    global _LOADED
    env_path = Path(path or DEFAULT_ENV_PATH)
    if _LOADED or not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    _LOADED = True
