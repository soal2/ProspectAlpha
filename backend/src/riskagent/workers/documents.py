"""Durable document parsing dispatch isolated from the API process."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path


_ACTIVE_PROCESSES: dict[str, subprocess.Popen] = {}
_ACTIVE_PROCESSES_LOCK = threading.Lock()
_DISPATCHER_STARTED = False
logger = logging.getLogger(__name__)


def dispatch_document_job(document_id: str, *, mode: str | None = None) -> str:
    """Dispatch one MinerU job without loading its models into the API process."""

    selected_mode = mode or os.getenv("DOCUMENT_PARSING_EXECUTION_MODE", "subprocess")
    if selected_mode == "rq":
        try:
            return _dispatch_rq(document_id)
        except Exception as exc:
            _record_dispatch_failure(document_id, exc)
            raise
    if selected_mode == "inline":
        run_document_job(document_id)
        return f"inline:{document_id}"
    if selected_mode != "subprocess":
        raise ValueError(
            "DOCUMENT_PARSING_EXECUTION_MODE must be subprocess, inline, or rq"
        )
    try:
        return _dispatch_subprocess(document_id)
    except Exception as exc:
        _record_dispatch_failure(document_id, exc)
        raise


def start_document_dispatcher() -> None:
    """Keep locally queued parses moving across API restarts."""

    global _DISPATCHER_STARTED
    if os.getenv("DOCUMENT_PARSING_EXECUTION_MODE", "subprocess") != "subprocess":
        return
    with _ACTIVE_PROCESSES_LOCK:
        if _DISPATCHER_STARTED:
            return
        _DISPATCHER_STARTED = True
    threading.Thread(
        target=_dispatcher_loop,
        name="document-parser-dispatcher",
        daemon=True,
    ).start()


def _dispatcher_loop() -> None:
    interval = max(
        5, int(os.getenv("DOCUMENT_PARSING_DISPATCH_INTERVAL_SECONDS", "10"))
    )
    while True:
        try:
            _dispatch_next_queued()
        except Exception:
            logger.exception("Failed to resume the local document parsing queue")
        threading.Event().wait(interval)


def run_document_job(document_id: str) -> None:
    from riskagent.documents import process_registered_prospectus

    stop_heartbeat = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_document_job,
        args=(document_id, stop_heartbeat),
        name=f"document-parser-heartbeat-{document_id}",
        daemon=True,
    )
    heartbeat.start()
    try:
        process_registered_prospectus(document_id)
    finally:
        stop_heartbeat.set()
        heartbeat.join(timeout=2)


def _heartbeat_document_job(document_id: str, stop: threading.Event) -> None:
    """Keep the processing lease alive even when the API process restarts."""

    from riskagent.documents import touch_prospectus_processing

    interval = max(
        5, int(os.getenv("DOCUMENT_PARSING_HEARTBEAT_SECONDS", "15"))
    )
    while not stop.is_set():
        try:
            touch_prospectus_processing(document_id)
        except Exception:
            # Parsing must not fail merely because one heartbeat write was delayed.
            pass
        stop.wait(interval)


def _dispatch_rq(document_id: str) -> str:
    try:
        import redis
        from rq import Queue
    except ImportError as exc:
        raise RuntimeError("RQ mode requires redis and rq packages to be installed.") from exc
    connection = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    queue = Queue(os.getenv("DOCUMENT_QUEUE_NAME", "documents"), connection=connection)
    job = queue.enqueue(run_document_job, document_id, job_timeout="2h")
    return str(job.id)


def _dispatch_subprocess(document_id: str) -> str:
    with _ACTIVE_PROCESSES_LOCK:
        active = _ACTIVE_PROCESSES.get(document_id)
        if active is not None and active.poll() is None:
            return f"subprocess-existing:{document_id}:{active.pid}"
        running_ids = {
            active_id
            for active_id, process in _ACTIVE_PROCESSES.items()
            if process.poll() is None
        }
        running_ids.update(_database_processing_document_ids())
        max_concurrent = max(
            1, int(os.getenv("DOCUMENT_PARSING_MAX_CONCURRENCY", "1"))
        )
        if len(running_ids) >= max_concurrent:
            return f"subprocess-queued:{document_id}"
        process = _start_subprocess(document_id)
        _ACTIVE_PROCESSES[document_id] = process

    monitor = threading.Thread(
        target=_monitor_subprocess,
        args=(document_id, process),
        name=f"document-parser-monitor-{document_id}",
        daemon=True,
    )
    monitor.start()
    return f"subprocess:{document_id}:{process.pid}"


def _start_subprocess(document_id: str) -> subprocess.Popen:
    env = os.environ.copy()
    cpu_threads = os.getenv("DOCUMENT_PARSING_CPU_THREADS", "2").strip() or "2"
    env.setdefault("OMP_NUM_THREADS", cpu_threads)
    env.setdefault("MKL_NUM_THREADS", cpu_threads)
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    if os.getenv("MINERU_DEVICE", "auto").strip().lower() == "mps":
        env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    cuda_visible_devices = os.getenv("MINERU_CUDA_VISIBLE_DEVICES", "").strip()
    if cuda_visible_devices:
        env["CUDA_VISIBLE_DEVICES"] = cuda_visible_devices
    backend_root = Path(__file__).resolve().parents[3]
    return subprocess.Popen(
        [sys.executable, "-m", "riskagent.workers.documents", document_id],
        cwd=backend_root,
        env=env,
        start_new_session=True,
    )


def _monitor_subprocess(document_id: str, process: subprocess.Popen) -> None:
    timeout_seconds = max(
        60, int(os.getenv("DOCUMENT_PARSING_TASK_TIMEOUT_SECONDS", "7200"))
    )
    timed_out = False
    try:
        exit_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.terminate()
        try:
            exit_code = process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            exit_code = process.wait()
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES.pop(document_id, None)
    if exit_code != 0:
        from riskagent.documents import mark_prospectus_interrupted

        reason = (
            f"MinerU 解析超过 {timeout_seconds} 秒，任务已停止"
            if timed_out
            else f"MinerU 独立解析进程异常退出（exit_code={exit_code}）"
        )
        mark_prospectus_interrupted(
            document_id,
            message=f"{reason}，原始 PDF 已保留，可直接重新解析。",
        )
    _dispatch_next_queued()


def _dispatch_next_queued() -> None:
    from sqlalchemy import select

    from riskagent.database import SessionLocal
    from riskagent.models import ProspectusDocumentRow

    with SessionLocal() as session:
        document_id = session.scalar(
            select(ProspectusDocumentRow.document_id)
            .where(ProspectusDocumentRow.status.in_(("registered", "queued")))
            .order_by(ProspectusDocumentRow.created_at.asc())
            .limit(1)
        )
    if document_id:
        _dispatch_subprocess(str(document_id))


def _database_processing_document_ids() -> set[str]:
    from sqlalchemy import select

    from riskagent.database import SessionLocal
    from riskagent.models import ProspectusDocumentRow

    with SessionLocal() as session:
        return {
            str(document_id)
            for document_id in session.scalars(
                select(ProspectusDocumentRow.document_id).where(
                    ProspectusDocumentRow.status == "processing"
                )
            ).all()
        }


def _record_dispatch_failure(document_id: str, exc: Exception) -> None:
    from riskagent.documents import mark_prospectus_interrupted

    try:
        mark_prospectus_interrupted(
            document_id,
            message=(
                f"无法启动 MinerU 解析任务（{type(exc).__name__}），"
                "原始 PDF 已保留，请检查队列/进程配置后重新解析。"
            ),
        )
    except Exception:
        logger.exception("Failed to persist dispatch error for %s", document_id)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m riskagent.workers.documents DOCUMENT_ID")
    run_document_job(sys.argv[1])


if __name__ == "__main__":
    main()
