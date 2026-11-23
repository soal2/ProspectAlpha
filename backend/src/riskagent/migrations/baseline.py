"""Idempotent baseline catch-up for databases created before Alembic.

This runs inside revision ``0001_baseline``. It creates missing tables and adds
missing columns that belong to the baseline. It does not drop tables, columns,
or rows, and it does not add columns owned by later revisions.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, MetaData, Numeric, inspect, text
from sqlalchemy.sql.sqltypes import JSON, String, Text

# These columns exist on the current ORM but are added by 0003, not by the
# baseline catch-up. Creating them here would make a database at 0002 already
# look migrated.
POST_BASELINE_COLUMNS = {
    ("risk_score_snapshots", "content_hash"),
    ("backtest_candidate_samples", "pinned_score"),
}


def baseline_metadata() -> MetaData:
    from riskagent import models  # noqa: F401
    from riskagent.database import Base

    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    for table_name, column_name in POST_BASELINE_COLUMNS:
        copied = metadata.tables.get(table_name)
        if copied is None or column_name not in copied.c:
            continue
        copied._columns.remove(copied.c[column_name])
    return metadata


def upgrade_baseline(connection) -> None:
    metadata = baseline_metadata()
    metadata.create_all(bind=connection)
    _add_missing_columns(connection, metadata)


def baseline_schema_problems(connection) -> list[str]:
    """Return reasons this database is not yet the baseline schema."""

    metadata = baseline_metadata()
    inspector = inspect(connection)
    problems: list[str] = []
    existing_tables = set(inspector.get_table_names())
    for table_name in metadata.tables:
        if table_name not in existing_tables:
            problems.append(f"缺少表 {table_name}")
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column in metadata.tables[table_name].columns:
            if column.name not in existing:
                problems.append(f"{table_name} 缺少列 {column.name}")
    return problems


def _add_missing_columns(connection, metadata: MetaData) -> None:
    inspector = inspect(connection)
    for table in metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing or column.primary_key:
                continue
            connection.execute(text(_add_column_sql(connection, table.name, column)))


def _add_column_sql(connection, table_name: str, column) -> str:
    dialect = connection.dialect
    preparer = dialect.identifier_preparer
    type_sql = column.type.compile(dialect=dialect)
    parts = [
        "ALTER TABLE",
        preparer.quote(table_name),
        "ADD COLUMN",
        preparer.quote(column.name),
        type_sql,
    ]
    if column.nullable or _prefer_nullable(column):
        parts.append("NULL")
    else:
        parts.append(f"NOT NULL DEFAULT {_sql_default(column, dialect.name)}")
    return " ".join(parts)


def _prefer_nullable(column) -> bool:
    """JSON has no honest empty value that fits every column, so add it as NULL."""

    return isinstance(column.type, JSON)


def _sql_default(column, dialect_name: str) -> str:
    if isinstance(column.type, Boolean):
        return "0" if dialect_name == "sqlite" else "FALSE"
    if isinstance(column.type, (Integer, Float, Numeric)):
        return "0"
    if isinstance(column.type, (String, Text)):
        return "''"
    return "''"
