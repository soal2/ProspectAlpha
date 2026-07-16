"""RQ worker process for production analysis execution."""

from __future__ import annotations

import os


def redis_connection():
    import redis

    return redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def check_redis_ready() -> bool:
    connection = redis_connection()
    return bool(connection.ping())


def make_worker():
    from rq import Queue, Worker

    connection = redis_connection()
    queue_names = list(
        dict.fromkeys(
            [
                os.getenv("ANALYSIS_QUEUE_NAME", "analysis"),
                os.getenv("DOCUMENT_QUEUE_NAME", "documents"),
            ]
        )
    )
    queues = [Queue(queue_name, connection=connection) for queue_name in queue_names]
    return Worker(queues, connection=connection)


def main() -> None:
    check_redis_ready()
    worker = make_worker()
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
