"""CLI: rebuild all ProspectAlpha tables and reseed reference data.

Usage:
    cd backend && PYTHONPATH=src python -m scripts.reset_database --yes

The development schema is dropped and recreated from current ORM metadata so
intentional no-migration contract replacements take effect. Afterwards the
``seed_reference_data`` helper re-populates
``agent_experts`` / ``agent_specs`` / ``risk_rule_sets`` / ``risk_rules`` /
``risk_rule_dimensions`` from the in-tree seed constants.
"""

from __future__ import annotations

import argparse
import os
import sys
from sqlalchemy import inspect, text

import riskagent.models  # noqa: F401  # register every table in Base.metadata
from riskagent.database import Base, session_factory_from_url
from riskagent.seed_data import seed_reference_data


def reset_database(database_url: str) -> dict[str, int]:
    """Drop and recreate the development schema, then reseed reference data.

    Returns a mapping of ``{table_name: deleted_row_count}`` for observability.
    """
    session_factory = session_factory_from_url(database_url)
    deleted: dict[str, int] = {}
    engine = session_factory.kw["bind"]
    inspector = inspect(engine)
    with engine.connect() as connection:
        for table in Base.metadata.sorted_tables:
            if inspector.has_table(table.name):
                deleted[table.name] = int(
                    connection.execute(text(f'SELECT COUNT(*) FROM "{table.name}"')).scalar_one()
                )
            else:
                deleted[table.name] = 0
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    # seed_reference_data is idempotent: it only inserts when the target table
    # is empty (or stale for agent_specs), so it cleanly rebuilds the catalog.
    seed_reference_data(session_factory)
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
            f"This will DROP and rebuild {len(Base.metadata.tables)} tables at {db_url}. "
            "Type RESET to proceed: "
        )
        if confirm.strip() != "RESET":
            print("Aborted.")
            return 1

    deleted = reset_database(db_url)
    for table_name, count in deleted.items():
        print(f"  {table_name}: deleted {count} rows")
    print("Re-seeded agent_experts / agent_specs / risk_rules.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
