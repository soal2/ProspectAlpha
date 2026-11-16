"""Versioned database upgrades.

Startup and ``python -m riskagent.migrations upgrade`` both call
``upgrade_database``. That is the only schema bootstrap. Do not add another
``create_all`` or hand-written ``ALTER`` on the application startup path.

``stamp-baseline`` is explicit. It records ``0001_baseline`` only when every
baseline table and column is already present. Columns added by later
revisions are not required for the stamp. It does not delete rows.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from riskagent.database import make_engine

BASELINE_REVISION = "0001_baseline"
MIGRATIONS_DIR = Path(__file__).resolve().parent


def upgrade_database(database_url: str | None = None) -> str:
    """Upgrade the ORM schema and any existing PostgreSQL RAG tables."""

    url = _normalize_url(database_url)
    command.upgrade(_config(url), "head")
    upgrade_rag_schema(url)
    revision = current_revision(url)
    if revision != head_revision():
        raise RuntimeError(f"迁移结束后修订为 {revision}，不是 {head_revision()}。")
    return revision


def stamp_baseline(database_url: str | None = None, *, confirm: bool = False) -> str:
    """Mark an already-current database as the baseline without running DDL."""

    if not confirm:
        raise RuntimeError(
            "给已有数据库打基线标记必须显式确认："
            "python -m riskagent.migrations stamp-baseline。"
            "这不会删除数据；只有表结构已经符合 0001_baseline 时才会写入 alembic_version。"
            "缺表或缺列时请改跑 upgrade。"
        )
    url = _normalize_url(database_url)
    engine = make_engine(url)
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
        if current == head_revision():
            return current
        if current is not None:
            raise RuntimeError(f"数据库已经在修订 {current}，不能再打基线。")
        from riskagent.migrations.baseline import baseline_schema_problems

        problems = baseline_schema_problems(connection)
    if problems:
        raise RuntimeError("当前库还不能打基线，请改跑 upgrade：" + "；".join(problems))
    command.stamp(_config(url), BASELINE_REVISION)
    # The empty extension revision is applied by upgrade, not by stamp. An
    # operator who only stamps must still run upgrade to reach head.
    command.upgrade(_config(url), "head")
    return current_revision(url)


def current_revision(database_url: str | None = None) -> str | None:
    url = _normalize_url(database_url)
    engine = make_engine(url)
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision() -> str:
    script = ScriptDirectory.from_config(_config("sqlite://"))
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("没有 Alembic head 修订。")
    return head


def upgrade_rag_schema(database_url: str) -> dict[str, str]:
    """Apply the existing pgvector column upgrade when that table already exists.

    The vector index definition stays in ``retrieval.index``. This function does
    not create a second copy of it and does not drop an existing index.
    """

    if not _is_postgres(database_url):
        return {"status": "not_required", "detail": "当前不是 PostgreSQL，跳过 RAG 迁移。"}
    from riskagent.retrieval.index import (
        DEFAULT_INDEX_SCHEMA,
        connect_postgres,
        ensure_content_sha256_column,
    )

    connection = connect_postgres(database_url)
    try:
        if not _rag_chunks_table_exists(connection, DEFAULT_INDEX_SCHEMA):
            return {"status": "not_required", "detail": "RAG 表尚未创建。"}
        ensure_content_sha256_column(schema=DEFAULT_INDEX_SCHEMA, conn=connection)
    finally:
        connection.close()
    return {"status": "ok", "detail": "已核对 content_sha256。"}


def rag_status(database_url: str | None = None) -> dict[str, str]:
    url = _normalize_url(database_url)
    if not _is_postgres(url):
        return {"status": "not_required", "detail": "当前不是 PostgreSQL，不检查 pgvector。"}
    try:
        from riskagent.retrieval.index import DEFAULT_INDEX_SCHEMA, connect_postgres

        connection = connect_postgres(url)
    except Exception as exc:
        return {"status": "failed", "detail": f"RAG 数据库不可用：{type(exc).__name__}: {exc}"}
    try:
        schema = DEFAULT_INDEX_SCHEMA
        if not _rag_chunks_table_exists(connection, schema):
            return {"status": "not_required", "detail": "RAG 表尚未创建。"}
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = 'prospectus_chunks'
                  AND column_name = 'content_sha256'
                """,
                (schema,),
            )
            if cursor.fetchone() is None:
                return {
                    "status": "failed",
                    "detail": "prospectus_chunks 缺少 content_sha256，迁移未完成。",
                }
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}
    finally:
        connection.close()


def _rag_chunks_table_exists(connection, schema: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = 'prospectus_chunks'
            """,
            (schema,),
        )
        return cursor.fetchone() is not None


def _config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _normalize_url(database_url: str | None) -> str:
    if database_url:
        return database_url
    from riskagent.database import get_database_url

    return get_database_url()


def _is_postgres(database_url: str) -> bool:
    return make_url(database_url).drivername.startswith("postgresql")
