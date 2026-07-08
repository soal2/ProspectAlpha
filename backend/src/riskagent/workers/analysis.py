"""Analysis job dispatch and worker entrypoint.

Production deployments can set ``ANALYSIS_EXECUTION_MODE=rq`` to enqueue jobs
through Redis/RQ.  Local tests use the inline mode to keep verification
deterministic without a running Redis service.
"""

from __future__ import annotations

import os
from typing import Protocol


class AnalysisExecutor(Protocol):
    def execute_queued(self, run_id: str) -> None:
        ...


def dispatch_analysis_job(run_id: str, executor: AnalysisExecutor) -> str:
    mode = os.getenv("ANALYSIS_EXECUTION_MODE", "inline")
    if mode == "rq":
        return _dispatch_rq(run_id)
    executor.execute_queued(run_id)
    return f"inline:{run_id}"


def run_analysis_job(run_id: str) -> None:
    from riskagent.analysis_runs import get_default_analysis_repository

    get_default_analysis_repository().execute_queued(run_id)


def _dispatch_rq(run_id: str) -> str:
    try:
        import redis
        from rq import Queue
    except ImportError as exc:
        raise RuntimeError("RQ mode requires redis and rq packages to be installed.") from exc
    connection = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    queue = Queue(os.getenv("ANALYSIS_QUEUE_NAME", "analysis"), connection=connection)
    job = queue.enqueue(run_analysis_job, run_id, job_timeout="30m")
    return str(job.id)
