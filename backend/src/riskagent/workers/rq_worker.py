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

    from riskagent.workers.heartbeat import configured_rq_queue_names

    connection = redis_connection()
    queues = [Queue(queue_name, connection=connection) for queue_name in configured_rq_queue_names()]
    return Worker(queues, connection=connection)


def main() -> None:
    check_redis_ready()
    from riskagent.workers.heartbeat import configured_rq_queue_names, start_heartbeat

    # Heartbeat the same queues Worker.work() will block on, including during a long job.
    start_heartbeat(queues=configured_rq_queue_names())
    worker = make_worker()
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
