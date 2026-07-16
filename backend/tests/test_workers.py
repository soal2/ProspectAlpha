import sys
import threading
import time
import types

import pytest

from riskagent.workers.analysis import dispatch_analysis_job
from riskagent.workers import rq_worker
from riskagent.workers import documents as document_workers


class InlineExecutor:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute_queued(self, run_id: str) -> None:
        self.executed.append(run_id)


def test_dispatch_inline_executes_immediately(monkeypatch):
    monkeypatch.setenv("ANALYSIS_EXECUTION_MODE", "inline")
    executor = InlineExecutor()

    job_id = dispatch_analysis_job("run_inline", executor)

    assert job_id == "inline:run_inline"
    assert executor.executed == ["run_inline"]


def test_dispatch_rejects_unknown_execution_mode():
    with pytest.raises(ValueError, match="ANALYSIS_EXECUTION_MODE"):
        dispatch_analysis_job("run_invalid", InlineExecutor(), mode="typo")


def test_dispatch_thread_executes_once_for_active_run(monkeypatch):
    monkeypatch.setenv("ANALYSIS_EXECUTION_MODE", "thread")
    release = threading.Event()

    class BlockingExecutor(InlineExecutor):
        def execute_queued(self, run_id: str) -> None:
            self.executed.append(run_id)
            release.wait(timeout=2)

    executor = BlockingExecutor()

    job_id = dispatch_analysis_job("run_thread", executor)
    for _ in range(50):
        if executor.executed:
            break
        time.sleep(0.02)

    duplicate_job_id = dispatch_analysis_job("run_thread", executor)
    release.set()

    assert job_id == "thread:run_thread"
    assert duplicate_job_id == "thread-existing:run_thread"
    assert executor.executed == ["run_thread"]


def test_dispatch_rq_enqueues_job_with_configured_queue(monkeypatch):
    enqueued = {}

    class FakeRedisModule:
        @staticmethod
        def from_url(url):
            enqueued["redis_url"] = url
            return "connection"

    class FakeJob:
        id = "job_123"

    class FakeQueue:
        def __init__(self, name, connection):
            enqueued["queue_name"] = name
            enqueued["connection"] = connection

        def enqueue(self, func, run_id, job_timeout):
            enqueued["func_name"] = func.__name__
            enqueued["run_id"] = run_id
            enqueued["job_timeout"] = job_timeout
            return FakeJob()

    fake_rq = types.SimpleNamespace(Queue=FakeQueue)
    monkeypatch.setitem(sys.modules, "redis", FakeRedisModule)
    monkeypatch.setitem(sys.modules, "rq", fake_rq)
    monkeypatch.setenv("ANALYSIS_EXECUTION_MODE", "rq")
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/2")
    monkeypatch.setenv("ANALYSIS_QUEUE_NAME", "critical-analysis")

    job_id = dispatch_analysis_job("run_rq", InlineExecutor())

    assert job_id == "job_123"
    assert enqueued == {
        "redis_url": "redis://example:6379/2",
        "queue_name": "critical-analysis",
        "connection": "connection",
        "func_name": "run_analysis_job",
        "run_id": "run_rq",
        "job_timeout": "30m",
    }


def test_rq_worker_builds_worker_for_configured_queue(monkeypatch):
    built = {}

    class FakeQueue:
        def __init__(self, name, connection):
            self.name = name
            self.connection = connection
            built["queue_name"] = name

    class FakeWorker:
        def __init__(self, queues, connection):
            built["queues"] = queues
            built["connection"] = connection

    fake_rq = types.SimpleNamespace(Queue=FakeQueue, Worker=FakeWorker)
    monkeypatch.setitem(sys.modules, "rq", fake_rq)
    monkeypatch.setattr(rq_worker, "redis_connection", lambda: "connection")
    monkeypatch.setenv("ANALYSIS_QUEUE_NAME", "analysis-workers")

    worker = rq_worker.make_worker()

    assert isinstance(worker, FakeWorker)
    assert built["queue_name"] == "documents"
    assert built["connection"] == "connection"
    assert built["queues"][0].name == "analysis-workers"
    assert built["queues"][1].name == "documents"


def test_document_rq_dispatch_uses_dedicated_queue(monkeypatch):
    enqueued = {}

    class FakeRedisModule:
        @staticmethod
        def from_url(url):
            enqueued["redis_url"] = url
            return "connection"

    class FakeJob:
        id = "document_job"

    class FakeQueue:
        def __init__(self, name, connection):
            enqueued["queue_name"] = name
            enqueued["connection"] = connection

        def enqueue(self, func, document_id, job_timeout):
            enqueued["func_name"] = func.__name__
            enqueued["document_id"] = document_id
            enqueued["job_timeout"] = job_timeout
            return FakeJob()

    monkeypatch.setitem(sys.modules, "redis", FakeRedisModule)
    monkeypatch.setitem(sys.modules, "rq", types.SimpleNamespace(Queue=FakeQueue))
    monkeypatch.setenv("DOCUMENT_QUEUE_NAME", "pdf-parsing")

    job_id = document_workers.dispatch_document_job("doc_1", mode="rq")

    assert job_id == "document_job"
    assert enqueued["queue_name"] == "pdf-parsing"
    assert enqueued["func_name"] == "run_document_job"
    assert enqueued["document_id"] == "doc_1"
    assert enqueued["job_timeout"] == "2h"
