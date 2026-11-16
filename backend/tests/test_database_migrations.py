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
    assert current_revision(url) == "0002_extension_pattern"
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


def test_score_snapshot_revision_follows_extension_pattern(tmp_path):
    url = f"sqlite:///{tmp_path / 'scores.db'}"
    command.upgrade(_config(url), "0002_extension_pattern")
    engine = create_engine(url)
    with engine.connect() as connection:
        before = {row[1] for row in connection.execute(text("PRAGMA table_info(risk_score_snapshots)"))}
    assert "content_hash" not in before
    assert upgrade_database(url) == "0003_score_snapshot_version"
    with engine.connect() as connection:
        after = {row[1] for row in connection.execute(text("PRAGMA table_info(risk_score_snapshots)"))}
        samples = {row[1] for row in connection.execute(text("PRAGMA table_info(backtest_candidate_samples)"))}
    assert "content_hash" in after
    assert "pinned_score" in samples
    command.downgrade(_config(url), "-1")
    assert current_revision(url) == "0002_extension_pattern"


def test_readiness_skips_redis_unless_rq_is_configured(tmp_path):
    url = f"sqlite:///{tmp_path / 'ready.db'}"
    upgrade_database(url)
    for modes in (
        {"analysis": "inline", "documents": "subprocess"},
        {"analysis": "thread", "documents": "subprocess"},
        {"analysis": "inline", "documents": "inline"},
    ):
        report = readiness_report(database_url=url, modes=modes)
        assert report["ready"] is True
        assert report["checks"]["redis"]["status"] == "not_required"
        assert report["checks"]["worker"]["status"] == "not_required"

    class MemoryRedis:
        def __init__(self, values):
            self.values = values

        def ping(self):
            return True

        def get(self, key):
            return self.values.get(key)

    missing = readiness_report(
        database_url=url,
        modes={"analysis": "rq", "documents": "subprocess"},
        redis_client=MemoryRedis({}),
    )
    assert missing["ready"] is False
    assert missing["checks"]["worker"]["queues"] == ["analysis"]
    assert "不表示部署里没有 worker 进程" in missing["checks"]["worker"]["detail"]

    from riskagent.readiness import public_readiness_body

    public = public_readiness_body(missing)
    assert public["ready"] is False
    assert public["checks"]["worker"] == "failed"
    assert "detail" not in public
    assert "不表示" not in str(public)


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
    assert set(ready.json()) == {"ready", "checks"}
    assert ready.json()["checks"]["redis"] == "not_required"


def test_liveness_stays_ok_when_rq_heartbeat_is_missing(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'live.db'}"
    upgrade_database(url)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ANALYSIS_EXECUTION_MODE", "rq")
    monkeypatch.setenv("DOCUMENT_PARSING_EXECUTION_MODE", "subprocess")

    def redis_down():
        raise ConnectionError("redis down")

    monkeypatch.setattr("riskagent.workers.rq_worker.redis_connection", redis_down)
    from riskagent.api.main import create_app

    client = TestClient(create_app())
    health = client.get("/health")
    ready = client.get("/ready")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "database": "ready"}
    assert ready.status_code == 503
    assert ready.json()["ready"] is False
    assert ready.json()["checks"]["worker"] == "failed"
    assert "redis down" not in ready.text


def test_upgrade_does_not_rewrite_historical_block_coordinates(tmp_path):
    url = f"sqlite:///{tmp_path / 'blocks.db'}"
    engine = create_engine(url)
    stored_bbox = "[0.0, 0.0, 595.0, 842.0]"
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE document_blocks (
                    block_id VARCHAR(128) PRIMARY KEY,
                    document_id VARCHAR(64),
                    page_number INTEGER NOT NULL,
                    section_path VARCHAR(512) NOT NULL,
                    block_type VARCHAR(64) NOT NULL,
                    text TEXT NOT NULL,
                    bbox JSON NOT NULL,
                    char_start INTEGER NOT NULL,
                    char_end INTEGER NOT NULL,
                    source_hash VARCHAR(64) NOT NULL,
                    parser_version VARCHAR(128) NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO document_blocks (
                    block_id, document_id, page_number, section_path, block_type,
                    text, bbox, char_start, char_end, source_hash, parser_version
                ) VALUES (
                    'block_old', 'doc_old', 4, '正文', 'text',
                    '旧文本', :bbox, 0, 900, 'hash', 'local-text-parser-v1'
                )
                """
            ),
            {"bbox": stored_bbox},
        )
    upgrade_database(url)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT page_number, bbox, char_start, char_end, parser_version "
                "FROM document_blocks WHERE block_id = 'block_old'"
            )
        ).one()
    assert row.page_number == 4
    assert row.bbox == stored_bbox
    assert row.char_start == 0
    assert row.char_end == 900
    assert row.parser_version == "local-text-parser-v1"


@pytest.mark.skipif(
    not os.getenv("PROSPECTALPHA_TEST_POSTGRES_URL"),
    reason="未配置 PROSPECTALPHA_TEST_POSTGRES_URL，跳过在线 PostgreSQL 升级。",
)
def test_postgres_upgrade_preserves_rows():
    url = os.environ["PROSPECTALPHA_TEST_POSTGRES_URL"]
    assert upgrade_database(url) == head_revision()
