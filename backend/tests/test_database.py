from sqlalchemy import Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

from riskagent.database import Base, check_database_connection, make_engine, session_scope


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
