"""The RQ worker writes queue-scoped heartbeats and readiness rejects stale ones."""

import json
import time

from riskagent.readiness import readiness_report
from riskagent.workers.heartbeat import (
    configured_rq_queue_names,
    encode_heartbeat,
    heartbeat_is_fresh,
    heartbeat_key,
    start_heartbeat,
)
from riskagent.workers.rq_worker import make_worker


class MemoryRedis:
    def __init__(self):
        self.values: dict[str, tuple[str, int | None]] = {}

    def ping(self):
        return True

    def set(self, key, value, ex=None):
        self.values[key] = (value, ex)

    def get(self, key):
        item = self.values.get(key)
        return None if item is None else item[0]


def test_make_worker_and_heartbeat_use_the_same_queue_names(monkeypatch):
    monkeypatch.setenv("ANALYSIS_QUEUE_NAME", "analysis-a")
    monkeypatch.setenv("DOCUMENT_QUEUE_NAME", "docs-b")
    created: list[str] = []

    class Queue:
        def __init__(self, name, connection=None):
            created.append(name)

    class Worker:
        def __init__(self, queues, connection=None):
            self.queues = queues

    monkeypatch.setattr("rq.Queue", Queue)
    monkeypatch.setattr("rq.Worker", Worker)
    monkeypatch.setattr("riskagent.workers.rq_worker.redis_connection", lambda: object())

    make_worker()
    assert created == ["analysis-a", "docs-b"]
    assert created == configured_rq_queue_names()


def test_heartbeat_refreshes_during_a_blocking_job_and_sets_ttl():
    client = MemoryRedis()
    stop = start_heartbeat(
        client=client,
        queues=["analysis", "documents"],
        interval_seconds=0.05,
        ttl_seconds=30,
    )
    try:
        time.sleep(0.18)
    finally:
        stop.set()
    analysis = client.values[heartbeat_key("analysis")]
    documents = client.values[heartbeat_key("documents")]
    assert analysis[1] == 30
    assert documents[1] == 30
    assert json.loads(analysis[0])["queue"] == "analysis"
    assert json.loads(documents[0])["queue"] == "documents"
    # Immediate write plus refreshes while this thread was blocked.
    assert client.values[heartbeat_key("analysis")][0]


def test_stale_or_wrong_queue_heartbeat_is_not_ready(tmp_path):
    from riskagent.migrations import upgrade_database

    url = f"sqlite:///{tmp_path / 'ready.db'}"
    upgrade_database(url)
    now = time.time()
    fresh = encode_heartbeat("analysis", now)
    stale = encode_heartbeat("analysis", now - 31)
    other = encode_heartbeat("documents", now)
    assert heartbeat_is_fresh(fresh, queue_name="analysis", now=now)
    assert not heartbeat_is_fresh(stale, queue_name="analysis", now=now)
    assert not heartbeat_is_fresh(other, queue_name="analysis", now=now)
    assert not heartbeat_is_fresh("1763251200", queue_name="analysis", now=now)

    class Reader:
        def __init__(self, values):
            self.values = values

        def ping(self):
            return True

        def get(self, key):
            return self.values.get(key)

    modes = {"analysis": "rq", "documents": "subprocess"}
    stale_report = readiness_report(
        database_url=url,
        modes=modes,
        redis_client=Reader({heartbeat_key("analysis"): stale}),
    )
    assert stale_report["ready"] is False
    assert stale_report["checks"]["worker"]["queues"] == ["analysis"]

    wrong_queue = readiness_report(
        database_url=url,
        modes=modes,
        redis_client=Reader({heartbeat_key("documents"): other}),
    )
    assert wrong_queue["ready"] is False

    fresh_report = readiness_report(
        database_url=url,
        modes=modes,
        redis_client=Reader({heartbeat_key("analysis"): fresh}),
    )
    assert fresh_report["ready"] is True
    assert fresh_report["checks"]["worker"]["queues"] == ["analysis"]


def test_refresh_count_grows_while_the_caller_is_busy():
    client = MemoryRedis()
    writes: list[str] = []
    original = client.set

    def record(key, value, ex=None):
        if key == heartbeat_key("analysis"):
            writes.append(value)
        original(key, value, ex=ex)

    client.set = record
    stop = start_heartbeat(client=client, queues=["analysis"], interval_seconds=0.05, ttl_seconds=1)
    try:
        time.sleep(0.2)
    finally:
        stop.set()
    assert len(writes) >= 3
    assert all(json.loads(item)["queue"] == "analysis" for item in writes)
