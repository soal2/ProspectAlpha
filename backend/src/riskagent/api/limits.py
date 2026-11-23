"""Configurable request and object limits.

Defaults cap upload and JSON payload size. Stored-object counts stay unlimited
until an operator sets a positive limit, so an existing deployment is not
given a quota it has not chosen.
"""

from __future__ import annotations

import os

from fastapi import HTTPException, status


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def max_upload_bytes() -> int:
    return max(_env_int("API_MAX_UPLOAD_BYTES", 80 * 1024 * 1024), 1)


def max_json_bytes() -> int:
    return max(_env_int("API_MAX_JSON_BYTES", 8 * 1024 * 1024), 1)


def max_stored_documents() -> int:
    return max(_env_int("API_MAX_STORED_DOCUMENTS", 0), 0)


def max_analysis_runs() -> int:
    return max(_env_int("API_MAX_ANALYSIS_RUNS", 0), 0)


def ensure_upload_within_limit(content: bytes) -> None:
    if len(content) > max_upload_bytes():
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="上传超过大小限制。",
        )


def ensure_within_count(current: int, limit: int, label: str) -> None:
    if limit > 0 and current >= limit:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"已达到{label}数量上限（{limit}）。",
        )
