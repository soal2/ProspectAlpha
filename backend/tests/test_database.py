from sqlalchemy import Integer, String, inspect, select, text
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import NullPool

from riskagent.database import (
    Base,
    check_database_connection,
    ensure_database_schema,
    make_engine,
    session_scope,
)


class DatabaseSmokeRow(Base):
    __tablename__ = "database_smoke_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))


def _session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'runtime.db'}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def test_check_database_connection(tmp_path):
    assert check_database_connection(_session_factory(tmp_path)) is True


def test_supabase_transaction_pooler_uses_null_pool():
    engine = make_engine(
        "postgresql+psycopg://app.project:password@"
        "aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres?sslmode=require"
    )

    assert isinstance(engine.pool, NullPool)


def test_session_scope_commits(tmp_path):
    session_factory = _session_factory(tmp_path)

    with session_scope(session_factory) as session:
        session.add(DatabaseSmokeRow(id=1, name="committed"))

    with session_factory() as session:
        row = session.scalar(select(DatabaseSmokeRow).where(DatabaseSmokeRow.id == 1))

    assert row is not None
    assert row.name == "committed"


def test_session_scope_rolls_back_on_error(tmp_path):
    session_factory = _session_factory(tmp_path)

    try:
        with session_scope(session_factory) as session:
            session.add(DatabaseSmokeRow(id=1, name="rolled-back"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    with session_factory() as session:
        row = session.scalar(select(DatabaseSmokeRow).where(DatabaseSmokeRow.id == 1))

    assert row is None


def test_existing_prospectus_table_gets_processing_diagnostic_columns(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE prospectus_documents ("
                "document_id VARCHAR(64) PRIMARY KEY, status VARCHAR(64) NOT NULL)"
            )
        )

    ensure_database_schema(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("prospectus_documents")}
    assert {
        "processing_error_code",
        "processing_error_message",
        "processing_attempts",
        "processing_started_at",
        "processing_heartbeat_at",
        "processing_finished_at",
    } <= columns
