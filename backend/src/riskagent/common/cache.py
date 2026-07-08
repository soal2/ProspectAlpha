"""Small TTL cache used by the local MVP pipeline.

The production roadmap expects Redis-backed queues and cache cleanup.  The
current backend intentionally has no Redis dependency, so this cache gives the
pipeline deterministic expiry and explicit pruning semantics for tests.
"""

from __future__ import annotations

import gc
import time
from dataclasses import dataclass
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    value: T
    expires_at: float


class TTLCache(Generic[T]):
    def __init__(self, *, ttl_seconds: float = 60.0, max_items: int = 512) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._items: dict[str, _Entry[T]] = {}

    def get(self, key: str) -> T | None:
        self.prune_expired()
        entry = self._items.get(key)
        return entry.value if entry else None

    def set(self, key: str, value: T) -> None:
        self.prune_expired()
        if len(self._items) >= self.max_items:
            oldest_key = min(self._items, key=lambda item: self._items[item].expires_at)
            self._items.pop(oldest_key, None)
        self._items[key] = _Entry(value=value, expires_at=time.monotonic() + self.ttl_seconds)

    def prune_expired(self) -> int:
        now = time.monotonic()
        expired = [key for key, entry in self._items.items() if entry.expires_at <= now]
        for key in expired:
            self._items.pop(key, None)
        return len(expired)

    def clear(self) -> None:
        self._items.clear()

    def collect_garbage(self) -> int:
        self.prune_expired()
        return gc.collect()

    def __len__(self) -> int:
        self.prune_expired()
        return len(self._items)
