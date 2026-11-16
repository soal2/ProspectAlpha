"""Alembic upgrades keep existing rows and do not treat the baseline as reversible."""

from __future__ import annotations

import os

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from riskagent.migrations import (
    _config,
    current_revision,
    head_revision,
    stamp_baseline,
    upgrade_database,
)
from riskagent.readiness import readiness_report


def test_upgrade_preserves_historic_rows_and_blocks_baseline_downgrade(tmp_path):
    url = f"sqlite:///{tmp_path / 'historic.db'}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE prospectus_documents (
                    document_id VARCHAR(64) PRIMARY KEY,
                    doc_key VARCHAR(128) NOT NULL,
                    original_filename VARCHAR(512) NOT NULL,
                    raw_pdf_path VARCHAR(1024) NOT NULL,
                    content_type VARCHAR(128) NOT NULL,
                    file_sha256 VARCHAR(64) NOT NULL,
                    file_size_bytes INTEGER NOT NULL,
                    status VARCHAR(64) NOT NULL,
                    created_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO prospectus_documents (
                    document_id, doc_key, original_filename, raw_pdf_path,
                    content_type, file_sha256, file_size_bytes, status
                ) VALUES (
                    'doc_old', 'old-key', 'old.pdf', '/tmp/old.pdf',
                    'application/pdf', 'abc', 12, 'registered'
                )
                """
            )
        )

    assert upgrade_database(url) == head_revision()
    assert upgrade_database(url) == head_revision()
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT document_id, status, processing_attempts "
                "FROM prospectus_documents WHERE document_id = 'doc_old'"
            )
        ).one()
    assert row.document_id == "doc_old"
    assert row.status == "registered"
    assert row.processing_attempts == 0

    command.downgrade(_config(url), "-1")
    assert current_revision(url) == "0001_baseline"
    with pytest.raises(RuntimeError, match="不受支持"):
        command.downgrade(_config(url), "base")
    assert current_revision(url) == "0001_baseline"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM prospectus_documents")).scalar_one() == 1


def test_stamp_baseline_is_explicit_and_refuses_an_empty_database(tmp_path):
    empty_url = f"sqlite:///{tmp_path / 'empty.db'}"
    create_engine(empty_url)
    with pytest.raises(RuntimeError, match="显式确认"):
        stamp_baseline(empty_url, confirm=False)
    with pytest.raises(RuntimeError, match="不能打基线"):
        stamp_baseline(empty_url, confirm=True)
    assert current_revision(empty_url) is None

    url = f"sqlite:///{tmp_path / 'current.db'}"
    upgrade_database(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
        connection.execute(
            text(
                "INSERT INTO prospectus_documents ("
                "document_id, doc_key, original_filename, raw_pdf_path, content_type, "
                "file_sha256, file_size_bytes, status, processing_attempts"
                ") VALUES ("
                "'doc_keep', 'keep', 'keep.pdf', '/tmp/keep.pdf', 'application/pdf', "
                "'def', 3, 'registered', 0)"
            )
        )
    assert stamp_baseline(url, confirm=True) == head_revision()
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT document_id FROM prospectus_documents WHERE document_id = 'doc_keep'")
        ).scalar_one() == "doc_keep"


def test_readiness_skips_redis_unless_rq_is_configured(tmp_path):
    url = f"sqlite:///{tmp_path / 'ready.db'}"
    upgrade_database(url)
    inline = readiness_report(
        database_url=url,
        modes={"analysis": "inline", "documents": "subprocess"},
    )
    assert inline["ready"] is True
    assert inline["checks"]["redis"]["status"] == "not_required"
    assert inline["checks"]["schema"]["revision"] == head_revision()

    class SilentRedis:
        def ping(self):
            return True

        def get(self, _key):
            return None

    missing = readiness_report(
        database_url=url,
        modes={"analysis": "rq", "documents": "subprocess"},
        redis_client=SilentRedis(),
    )
    assert missing["ready"] is False
    assert "不表示部署里没有 worker 进程" in missing["checks"]["worker"]["detail"]

    class LiveRedis(SilentRedis):
        def get(self, _key):
            return "1763251200"

    ready = readiness_report(
        database_url=url,
        modes={"analysis": "rq", "documents": "rq"},
        redis_client=LiveRedis(),
    )
    assert ready["ready"] is True
    assert ready["checks"]["worker"]["status"] == "ok"


def test_migration_failure_is_not_ready_and_health_stays_minimal(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'failed.db'}"
    upgrade_database(url)
    report = readiness_report(
        database_url=url,
        migration_error="RuntimeError: migration failed",
        modes={"analysis": "inline", "documents": "subprocess"},
    )
    assert report["ready"] is False
    assert report["checks"]["schema"]["status"] == "failed"

    monkeypatch.setenv("DATABASE_URL", url)
    from riskagent.api.main import create_app

    client = TestClient(create_app())
    health = client.get("/health")
    ready = client.get("/ready")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert set(health.json()) == {"status", "database"}
    assert ready.status_code == 200
    assert ready.json()["ready"] is True


@pytest.mark.skipif(
    not os.getenv("PROSPECTALPHA_TEST_POSTGRES_URL"),
    reason="未配置 PROSPECTALPHA_TEST_POSTGRES_URL，跳过在线 PostgreSQL 升级。",
)
def test_postgres_upgrade_preserves_rows():
    url = os.environ["PROSPECTALPHA_TEST_POSTGRES_URL"]
    assert upgrade_database(url) == head_revision()
