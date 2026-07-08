import sys
import types

from riskagent.workers.analysis import dispatch_analysis_job
from riskagent.workers import rq_worker


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
    assert built["queue_name"] == "analysis-workers"
    assert built["connection"] == "connection"
    assert built["queues"][0].name == "analysis-workers"
