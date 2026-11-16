"""Readiness is separate from the public liveness probe.

``GET /health`` stays a minimal process-and-database check. ``GET /ready``
checks the Alembic revision and, only when RQ is configured, Redis plus the
worker heartbeat. Inline and subprocess modes do not require Redis.
"""

from __future__ import annotations

from riskagent.migrations import current_revision, head_revision, rag_status
from riskagent.workers.heartbeat import (
    HEARTBEAT_KEY,
    execution_modes,
    read_heartbeat,
    redis_required,
)


def readiness_report(
    *,
    database_url: str | None = None,
    migration_error: str | None = None,
    modes: dict[str, str] | None = None,
    redis_client=None,
) -> dict[str, object]:
    selected_modes = modes or execution_modes()
    checks: dict[str, object] = {}
    ready = True
    if migration_error:
        checks["schema"] = {"status": "failed", "detail": migration_error}
        ready = False
    else:
        try:
            revision = current_revision(database_url)
            head = head_revision()
            if revision != head:
                checks["schema"] = {
                    "status": "failed",
                    "revision": revision,
                    "head": head,
                    "detail": "数据库修订未到达当前版本，不能视为就绪。",
                }
                ready = False
            else:
                checks["schema"] = {"status": "ok", "revision": revision}
        except Exception as exc:
            checks["schema"] = {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}
            ready = False

    rag = rag_status(database_url)
    checks["rag"] = rag
    if rag["status"] == "failed":
        ready = False

    if not redis_required(selected_modes):
        checks["redis"] = {
            "status": "not_required",
            "detail": "当前 analysis/document 模式不使用 Redis。",
        }
        checks["worker"] = {
            "status": "not_required",
            "detail": "inline、thread 或 subprocess 不要求 RQ worker 心跳。",
        }
    else:
        client = redis_client
        if client is None:
            try:
                from riskagent.workers.rq_worker import redis_connection

                client = redis_connection()
                client.ping()
            except Exception as exc:
                checks["redis"] = {
                    "status": "failed",
                    "detail": f"已配置 RQ，但 Redis 不可用：{type(exc).__name__}: {exc}",
                }
                checks["worker"] = {
                    "status": "failed",
                    "detail": (
                        "已配置 RQ。Render 启动脚本会拉起 worker；"
                        "Redis 不可用时无法观察心跳，这不表示部署里没有 worker 进程。"
                    ),
                }
                return {"ready": False, "checks": checks}
        else:
            try:
                client.ping()
            except Exception as exc:
                checks["redis"] = {
                    "status": "failed",
                    "detail": f"已配置 RQ，但 Redis 不可用：{type(exc).__name__}: {exc}",
                }
                checks["worker"] = {
                    "status": "failed",
                    "detail": "已配置 RQ，但还不能读取 worker 心跳。",
                }
                return {"ready": False, "checks": checks}
        checks["redis"] = {"status": "ok"}
        heartbeat = read_heartbeat(client)
        if not heartbeat:
            checks["worker"] = {
                "status": "failed",
                "detail": (
                    f"已配置 RQ。启动脚本会拉起 worker，但 Redis 键 {HEARTBEAT_KEY} "
                    "还没有新鲜心跳。这不表示部署里没有 worker 进程。"
                ),
            }
            ready = False
        else:
            checks["worker"] = {"status": "ok", "heartbeat_at": heartbeat}
    return {"ready": ready, "checks": checks}
