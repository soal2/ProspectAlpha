"""Vercel FastAPI entrypoint."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from riskagent.api.main import app

__all__ = ["app"]
