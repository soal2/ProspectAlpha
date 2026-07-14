"""In-memory runtime event fan-out for analysis run streams."""

from __future__ import annotations

import asyncio
from typing import Any


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._subscribers.setdefault(run_id, set()).add(queue)
        return queue

    async def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(run_id)
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(run_id, None)

    async def publish(self, run_id: str, event: dict[str, Any]) -> None:
        async with self._lock:
            subscribers = list(self._subscribers.get(run_id, set()))
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    def publish_sync(self, run_id: str, event: dict[str, Any]) -> None:
        try:
            asyncio.run(self.publish(run_id, event))
        except RuntimeError:
            pass
        except Exception:
            pass


_EVENT_BROKER: EventBroker | None = None


def get_event_broker() -> EventBroker:
    global _EVENT_BROKER
    if _EVENT_BROKER is None:
        _EVENT_BROKER = EventBroker()
    return _EVENT_BROKER
