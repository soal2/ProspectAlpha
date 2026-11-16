"""Queue-scoped RQ worker heartbeats.

Each process that calls ``prospect-alpha-worker`` refreshes one Redis key per
queue it consumes:

    prospectalpha:worker:heartbeat:<queue>

The value is ``{"queue": "<queue>", "beat_at": <unix seconds>}`` and the key
expires after ``HEARTBEAT_TTL_SECONDS``. A daemon thread keeps refreshing the
keys while ``Worker.work()`` is inside a long job. Inline, thread, and
subprocess execution do not write or require these keys.

Readiness treats a key as fresh only when it names the required queue and
``beat_at`` is within the TTL. A missing key, a different queue, or an older
timestamp is a stale worker, not proof that the deploy forgot to start one.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

HEARTBEAT_KEY_PREFIX = "prospectalpha:worker:heartbeat:"
HEARTBEAT_TTL_SECONDS = 30
HEARTBEAT_INTERVAL_SECONDS = 10
logger = logging.getLogger(__name__)


def heartbeat_key(queue_name: str) -> str:
    return f"{HEARTBEAT_KEY_PREFIX}{queue_name}"


def configured_rq_queue_names() -> list[str]:
    """Queues the RQ worker process actually consumes. Keep this in lockstep with ``make_worker``."""

    return list(
        dict.fromkeys(
            [
                os.getenv("ANALYSIS_QUEUE_NAME", "analysis"),
                os.getenv("DOCUMENT_QUEUE_NAME", "documents"),
            ]
        )
    )


def execution_modes() -> dict[str, str]:
    return {
        "analysis": os.getenv("ANALYSIS_EXECUTION_MODE", "inline"),
        "documents": os.getenv("DOCUMENT_PARSING_EXECUTION_MODE", "subprocess"),
    }


def required_queue_names(modes: dict[str, str] | None = None) -> list[str]:
    selected = modes or execution_modes()
    names: list[str] = []
    if selected.get("analysis") == "rq":
        names.append(os.getenv("ANALYSIS_QUEUE_NAME", "analysis"))
    if selected.get("documents") == "rq":
        names.append(os.getenv("DOCUMENT_QUEUE_NAME", "documents"))
    return list(dict.fromkeys(names))


def redis_required(modes: dict[str, str] | None = None) -> bool:
    return bool(required_queue_names(modes))


def encode_heartbeat(queue_name: str, beat_at: float) -> str:
    return json.dumps({"queue": queue_name, "beat_at": beat_at}, separators=(",", ":"))


def heartbeat_is_fresh(
    raw: Any,
    *,
    queue_name: str,
    now: float | None = None,
    ttl_seconds: int = HEARTBEAT_TTL_SECONDS,
) -> bool:
    """True only for this queue's own beat, younger than the TTL."""

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        payload = json.loads(raw)
        beat_at = float(payload["beat_at"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return False
    if payload.get("queue") != queue_name:
        return False
    current = time.time() if now is None else now
    age = current - beat_at
    return 0 <= age <= ttl_seconds


def start_heartbeat(
    *,
    client=None,
    queues: list[str] | None = None,
    interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
    ttl_seconds: int = HEARTBEAT_TTL_SECONDS,
) -> threading.Event:
    """Write a fresh key per queue immediately, then refresh until stopped.

    The returned event stops the daemon thread. The worker process can ignore
    it; process exit ends the thread. Tests set it so the thread does not
    outlive the assertion.
    """

    queue_names = list(queues) if queues is not None else configured_rq_queue_names()
    if interval_seconds <= 0:
        raise ValueError("heartbeat interval must be positive")
    if ttl_seconds <= interval_seconds:
        raise ValueError("heartbeat TTL must be longer than the refresh interval")
    connection = client if client is not None else _redis_client()
    stop = threading.Event()

    def beat() -> None:
        beat_at = time.time()
        for queue_name in queue_names:
            connection.set(
                heartbeat_key(queue_name),
                encode_heartbeat(queue_name, beat_at),
                ex=ttl_seconds,
            )

    try:
        beat()
    except Exception:
        logger.exception("worker heartbeat failed")

    def loop() -> None:
        while not stop.wait(interval_seconds):
            try:
                beat()
            except Exception:
                logger.exception("worker heartbeat failed")

    threading.Thread(target=loop, name="prospectalpha-worker-heartbeat", daemon=True).start()
    return stop


def _redis_client():
    from riskagent.workers.rq_worker import redis_connection

    return redis_connection()
