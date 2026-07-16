"""Database engine/session helpers for the backend runtime."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE_PATH = BACKEND_ROOT / "data" / "processed" / "prospect_alpha.db"


class Base(DeclarativeBase):
    pass


def get_database_url() -> str:
    configured = os.getenv("DATABASE_URL")
    if configured:
        return configured
    DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_SQLITE_PATH}"


def make_engine(database_url: str | None = None):
    url = database_url or get_database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


def session_factory_from_url(database_url: str) -> sessionmaker:
    """Create a sessionmaker bound to a fresh engine for the given URL.

    Mirrors :func:`make_engine` settings (sqlite check_same_thread, future=True)
    so callers can target an arbitrary database URL (e.g. in CLI tools and tests).
    """
    engine = make_engine(database_url)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def session_scope(session_factory: sessionmaker = SessionLocal) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_connection(session_factory: sessionmaker = SessionLocal) -> bool:
    with session_factory() as session:
        session.execute(text("SELECT 1"))
    return True


def init_db(database_url: str | None = None) -> None:
    """Create all tables for the default engine (``database_url`` is None) or an
    explicit URL. Returns nothing."""
    from riskagent import models  # noqa: F401

    active_engine = engine if database_url is None else make_engine(database_url)
    ensure_database_schema(active_engine)


def ensure_database_schema(active_engine) -> None:
    """Create current ORM tables and apply supported additive evolutions."""

    Base.metadata.create_all(bind=active_engine)
    _ensure_prospectus_processing_columns(active_engine)


def _ensure_prospectus_processing_columns(active_engine) -> None:
    """Apply the small additive schema evolution used without Alembic today."""

    inspector = inspect(active_engine)
    if "prospectus_documents" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("prospectus_documents")}
    additions = {
        "processing_error_code": "VARCHAR(64)",
        "processing_error_message": "TEXT",
        "processing_attempts": "INTEGER NOT NULL DEFAULT 0",
    }
    with active_engine.begin() as connection:
        for column, definition in additions.items():
            if column not in existing:
                if active_engine.dialect.name == "postgresql":
                    statement = (
                        f"ALTER TABLE prospectus_documents "
                        f"ADD COLUMN IF NOT EXISTS {column} {definition}"
                    )
                else:
                    statement = f"ALTER TABLE prospectus_documents ADD COLUMN {column} {definition}"
                connection.execute(
                    text(statement)
                )


def init_reference_data(session_factory: sessionmaker = SessionLocal) -> None:
    from riskagent import models  # noqa: F401

    bind = getattr(session_factory, "kw", {}).get("bind")
    if bind is None:
        init_db()
    else:
        Base.metadata.create_all(bind=bind)
    from riskagent.seed_data import seed_reference_data

    seed_reference_data(session_factory)
