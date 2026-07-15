"""Runtime event fan-out for analysis run streams.

The local queue keeps thread-mode tests and single-process development fast.  Redis
Pub/Sub bridges the API process and RQ workers for transient LLM deltas; durable
events remain in the analysis-run store for replay and recovery.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from time import monotonic
from typing import Any
from uuid import uuid4


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
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(self.publish(run_id, event))
            else:
                loop.create_task(self.publish(run_id, event))
        except Exception:
            pass
        try:
            import redis

            redis.from_url(_redis_url()).publish(_channel(run_id), json.dumps(event, ensure_ascii=False))
        except Exception:
            # Redis is an optional low-latency transport.  The durable event log
            # still lets clients recover complete events when it is unavailable.
            pass


def _redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _channel(run_id: str) -> str:
    return f"riskagent:analysis-events:{run_id}"


class RedisEventSubscription:
    def __init__(self, client: Any, pubsub: Any) -> None:
        self._client = client
        self._pubsub = pubsub

    async def get(self) -> dict[str, Any] | None:
        message = await self._pubsub.get_message(
            ignore_subscribe_messages=True,
            timeout=0.01,
        )
        if not message:
            return None
        data = message.get("data")
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return json.loads(data) if isinstance(data, str) else None

    async def close(self) -> None:
        await self._pubsub.close()
        await self._client.aclose()


async def subscribe_redis_events(run_id: str) -> RedisEventSubscription | None:
    """Return a cross-process subscription, or ``None`` when Redis is unavailable."""

    try:
        import redis.asyncio as redis

        client = redis.from_url(_redis_url())
        pubsub = client.pubsub()
        await pubsub.subscribe(_channel(run_id))
        return RedisEventSubscription(client, pubsub)
    except Exception:
        return None


class LiveLLMCall:
    """Emit a real model reply as transient deltas and one durable completion.

    ``persist`` is intentionally called only for terminal call events.  It is
    supplied by the analysis repository so this module stays independent of the
    analysis-run storage implementation.
    """

    def __init__(
        self,
        *,
        run_id: str,
        agent_name: str,
        provider: str,
        model: str,
        persist: Any,
        title: str = "正在等待模型回复",
    ) -> None:
        self.run_id = run_id
        self.agent_name = agent_name
        self.provider = provider
        self.model = model
        self.persist = persist
        self.call_id = f"llm_{uuid4().hex}"
        self._started_at = monotonic()
        self._sequence = 0
        self._pending = ""
        self._last_flush = self._started_at
        self._lock = threading.Lock()
        self._completed = False
        self._publish("llm_response_started", title, "已发起真实模型调用。")

    def delta(self, text: str) -> None:
        with self._lock:
            self._pending += text
            now = monotonic()
            if len(self._pending) < 160 and now - self._last_flush < 0.15:
                return
            self._flush_locked()

    def complete(self, raw_response: str) -> None:
        with self._lock:
            if self._completed:
                return
            self._flush_locked()
            self._completed = True
        event = self._event(
            "llm_response_completed",
            "模型回复已完成",
            "已收到并保存该 Agent 的完整真实模型回复。",
            raw_response=raw_response,
            duration_ms=round((monotonic() - self._started_at) * 1000),
        )
        self.persist(event)

    def fail(self, error: BaseException) -> None:
        with self._lock:
            if self._completed:
                return
            self._flush_locked()
            self._completed = True
        event = self._event(
            "llm_response_failed",
            "模型调用失败",
            f"{type(error).__name__}: {error}",
            duration_ms=round((monotonic() - self._started_at) * 1000),
            error=f"{type(error).__name__}: {error}",
        )
        self.persist(event)

    def _flush_locked(self) -> None:
        if not self._pending:
            return
        text = self._pending
        self._pending = ""
        self._last_flush = monotonic()
        self._sequence += 1
        self._publish(
            "llm_response_delta",
            "模型正在回复",
            text,
            delta=text,
            sequence=self._sequence,
        )

    def _publish(self, event_type: str, title: str, summary: str, **extra: Any) -> None:
        get_event_broker().publish_sync(self.run_id, self._event(event_type, title, summary, **extra))

    def _event(self, event_type: str, title: str, summary: str, **extra: Any) -> dict[str, Any]:
        return {
            "event_type": event_type,
            "actor_name": self.agent_name,
            "title": title,
            "summary": summary,
            "call_id": self.call_id,
            "provider": self.provider,
            "model": self.model,
            **extra,
        }


_EVENT_BROKER: EventBroker | None = None


def get_event_broker() -> EventBroker:
    global _EVENT_BROKER
    if _EVENT_BROKER is None:
        _EVENT_BROKER = EventBroker()
    return _EVENT_BROKER
