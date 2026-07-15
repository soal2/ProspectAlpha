"""Tests for the reset_database CLI.

Uses a tmp_path SQLite database so the test does not depend on a real
PostgreSQL instance. ``seed_reference_data`` is itself database-agnostic
(it only relies on the ORM models), so SQLite is sufficient to exercise
the truncate + reseed logic end-to-end.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from riskagent.database import init_db, session_factory_from_url
from riskagent.models import (
    AgentExpertRow,
    AgentSpecRow,
    AnalysisRunRow,
    DocumentAssetRow,
    EvidenceLibraryRow,
    RetrievalIndexRow,
    RiskRuleSetRow,
)
from riskagent.seed_data import AGENT_EXPERTS, AGENT_SPECS


def _seed_one_business_row(session_factory) -> None:
    """Insert one DocumentAssetRow covering all required (non-nullable) columns."""
    with session_factory() as session:
        session.add(
            DocumentAssetRow(
                document_id="d1",
                filename="test.pdf",
                content_type="application/pdf",
                file_sha256="0" * 64,
                storage_uri="file:///tmp/test.pdf",
                file_size_bytes=1,
                page_count=1,
                text_chars=1,
                block_count=0,
            )
        )
        session.commit()


def test_reset_database_clears_business_tables_and_reseeds(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path}/test_reset.db"
    init_db(db_url)
    session_factory = session_factory_from_url(db_url)

    # Initial seed should populate reference tables.
    from riskagent.seed_data import seed_reference_data

    seed_reference_data(session_factory)

    # Now pollute the business tables.
    _seed_one_business_row(session_factory)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DocumentAssetRow)) == 1
        assert session.scalar(select(func.count()).select_from(AgentExpertRow)) == len(AGENT_EXPERTS)

    # Import here so init_db has run first (ensures models import cleanly).
    from scripts.reset_database import reset_database

    deleted = reset_database(db_url)

    # All 10 tables were touched.
    assert len(deleted) == 10
    for table_name, count in deleted.items():
        assert isinstance(table_name, str)
        assert isinstance(count, int)

    # Business tables must be empty.
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DocumentAssetRow)) == 0
        assert session.scalar(select(func.count()).select_from(AnalysisRunRow)) == 0
        assert session.scalar(select(func.count()).select_from(EvidenceLibraryRow)) == 0
        assert session.scalar(select(func.count()).select_from(RetrievalIndexRow)) == 0

    # Reference tables must be re-populated to the seed constants' length.
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentExpertRow)) == len(AGENT_EXPERTS)
        assert session.scalar(select(func.count()).select_from(AgentSpecRow)) == len(AGENT_SPECS)
        assert session.scalar(select(func.count()).select_from(RiskRuleSetRow)) >= 1


def test_reset_database_is_idempotent(tmp_path: Path) -> None:
    """Calling reset twice should not crash or duplicate reference rows."""
    db_url = f"sqlite:///{tmp_path}/test_reset_idempotent.db"
    init_db(db_url)
    session_factory = session_factory_from_url(db_url)

    from scripts.reset_database import reset_database

    reset_database(db_url)
    deleted_again = reset_database(db_url)

    assert len(deleted_again) == 10
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentExpertRow)) == len(AGENT_EXPERTS)
        assert session.scalar(select(func.count()).select_from(AgentSpecRow)) == len(AGENT_SPECS)
