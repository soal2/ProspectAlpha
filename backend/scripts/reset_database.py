"""CLI: truncate ProspectAlpha analysis data while retaining reference catalogs.

Usage:
    cd backend && PYTHONPATH=src python -m scripts.reset_database --yes

Analysis, reporting, document-index and checkpoint tables are cleared in
reverse foreign-key order.  The live expert and risk-rule catalogs are not
deleted or re-seeded.
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import inspect, text

from riskagent.database import session_factory_from_url

REFERENCE_TABLES = frozenset(
    {
        "agent_experts",
        "agent_specs",
        "risk_rule_sets",
        "risk_rule_dimensions",
        "risk_rules",
    }
)


def _tables_in_reset_order(session_factory) -> list[tuple[str | None, str]]:
    """Return every current application table in reverse FK dependency order.

    The database deliberately has no migration registry for the reporting and
    checkpoint tables.  Discovering tables from the live schema means a reset
    cannot silently retain data when a new persistence module is added.
    """
    bind = session_factory.kw["bind"]
    inspector = inspect(bind)
    schemas: list[str | None] = [None]
    if bind.dialect.name == "postgresql":
        schemas = [schema for schema in ("public", "rag") if schema in inspector.get_schema_names()]
    remaining = {
        (schema, table)
        for schema in schemas
        for table in inspector.get_table_names(schema=schema)
        if not (schema in (None, "public") and table in REFERENCE_TABLES)
    }
    dependencies = {
        table: {
            (
                foreign_key.get("referred_schema") or table[0],
                foreign_key["referred_table"],
            )
            for foreign_key in inspector.get_foreign_keys(table[1], schema=table[0])
            if (
                foreign_key.get("referred_schema") or table[0],
                foreign_key.get("referred_table"),
            )
            in remaining
        }
        for table in remaining
    }
    ordered: list[str] = []
    while remaining:
        # Delete leaf/child tables first.  A cyclic legacy schema is still
        # deterministic; PostgreSQL currently has no such cycle.
        leaves = sorted(
            table
            for table in remaining
            if not any(table in dependencies[child] for child in remaining)
        )
        for table in leaves or [min(remaining)]:
            ordered.append(table)
            remaining.remove(table)
    return ordered


def _quoted_table_name(schema: str | None, table: str) -> str:
    quoted_table = f'"{table.replace("\"", "\"\"")}"'
    if schema is None:
        return quoted_table
    return f'"{schema.replace("\"", "\"\"")}".{quoted_table}'


def _table_label(schema: str | None, table: str) -> str:
    return table if schema in (None, "public") else f"{schema}.{table}"


def reset_database(database_url: str) -> dict[str, int]:
    """Delete current analysis data while retaining reference catalog rows.

    Returns a mapping of ``{table_name: deleted_row_count}`` for observability.
    """
    session_factory = session_factory_from_url(database_url)
    deleted: dict[str, int] = {}
    with session_factory() as session:
        for schema, table_name in _tables_in_reset_order(session_factory):
            result = session.execute(text(f"DELETE FROM {_quoted_table_name(schema, table_name)}"))
            deleted[_table_label(schema, table_name)] = result.rowcount or 0
        session.commit()
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset ProspectAlpha business tables.")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL (otherwise read from env).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    args = parser.parse_args()

    db_url = args.database_url or os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 2

    if not args.yes:
        confirm = input(
            f"This will DELETE all rows in every application table at {db_url}. "
            "Type RESET to proceed: "
        )
        if confirm.strip() != "RESET":
            print("Aborted.")
            return 1

    deleted = reset_database(db_url)
    for table_name, count in deleted.items():
        print(f"  {table_name}: deleted {count} rows")
    print("Retained agent_experts / agent_specs / risk_rules.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
