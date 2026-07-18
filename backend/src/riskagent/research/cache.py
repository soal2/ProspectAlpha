"""Query cache for public search results.

Values are strictly :class:`SearchResponse` (raw public search results), so
conclusions can never be reused across runs.  Cache keys already embed
``data_cutoff_date`` and policy (see ``gateway.cache_key_for``), so entries
never leak across cutoff dates.

The file cache mirrors the YAML store's per-path-lock + write-to-temp +
atomic-replace pattern (analysis_runs._write_yaml_atomic_unlocked).  It is
reimplemented locally because importing those private helpers would create a
research -> analysis_runs import cycle (analysis_runs imports this package).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .gateway import SearchResponse

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.absolute())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


@runtime_checkable
class QueryCache(Protocol):
    def get(self, key: str) -> Optional[SearchResponse]: ...

    def set(self, key: str, response: SearchResponse) -> None: ...


class InMemoryQueryCache:
    def __init__(self, *, ttl_seconds: Optional[int] = None) -> None:
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, tuple[float, SearchResponse]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[SearchResponse]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            stored_at, response = entry
            if self.ttl_seconds is not None and time.time() - stored_at > self.ttl_seconds:
                del self._entries[key]
                return None
            return response.model_copy(deep=True)

    def set(self, key: str, response: SearchResponse) -> None:
        with self._lock:
            self._entries[key] = (time.time(), response.model_copy(deep=True))


class FileQueryCache:
    def __init__(self, cache_dir: str | Path, *, ttl_seconds: int = 7 * 24 * 3600) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_seconds

    def _path_for(self, key: str) -> Path:
        safe = "".join(ch for ch in key if ch.isalnum() or ch in "-_")
        return self.cache_dir / f"{safe}.json"

    def get(self, key: str) -> Optional[SearchResponse]:
        path = self._path_for(key)
        with _path_lock(path):
            if not path.exists():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                stored_at = float(payload["stored_at"])
                if time.time() - stored_at > self.ttl_seconds:
                    path.unlink(missing_ok=True)
                    return None
                return SearchResponse.model_validate(payload["response"])
            except (KeyError, ValueError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                return None

    def set(self, key: str, response: SearchResponse) -> None:
        path = self._path_for(key)
        with _path_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            temp_path.write_text(
                json.dumps(
                    {"stored_at": time.time(), "response": response.model_dump(mode="json")},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            temp_path.replace(path)
