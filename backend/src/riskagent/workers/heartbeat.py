"""RQ worker heartbeat shared by the worker process and the readiness probe.

The key is ``prospectalpha:worker:heartbeat``. The worker refreshes it every
10 seconds with a 30 second expiry. Inline, thread, and subprocess execution
do not write or require this key.
"""

from __future__ import annotations

import logging
import os
import threading
import time

HEARTBEAT_KEY = "prospectalpha:worker:heartbeat"
HEARTBEAT_TTL_SECONDS = 30
HEARTBEAT_INTERVAL_SECONDS = 10
logger = logging.getLogger(__name__)


def execution_modes() -> dict[str, str]:
    return {
        "analysis": os.getenv("ANALYSIS_EXECUTION_MODE", "inline"),
        "documents": os.getenv("DOCUMENT_PARSING_EXECUTION_MODE", "subprocess"),
    }


def redis_required(modes: dict[str, str] | None = None) -> bool:
    selected = modes or execution_modes()
    return selected.get("analysis") == "rq" or selected.get("documents") == "rq"


def start_heartbeat(
    *,
    client=None,
    interval_seconds: int = HEARTBEAT_INTERVAL_SECONDS,
    ttl_seconds: int = HEARTBEAT_TTL_SECONDS,
) -> None:
    """Refresh the heartbeat until the process exits. The first write is immediate."""

    def beat(connection) -> None:
        connection.set(HEARTBEAT_KEY, str(time.time()), ex=ttl_seconds)

    connection = client if client is not None else _redis_client()
    try:
        beat(connection)
    except Exception:
        logger.exception("worker heartbeat failed")

    def loop() -> None:
        while True:
            time.sleep(interval_seconds)
            try:
                beat(connection)
            except Exception:
                logger.exception("worker heartbeat failed")

    threading.Thread(target=loop, name="prospectalpha-worker-heartbeat", daemon=True).start()


def read_heartbeat(client) -> str | None:
    value = client.get(HEARTBEAT_KEY)
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _redis_client():
    from riskagent.workers.rq_worker import redis_connection

    return redis_connection()
