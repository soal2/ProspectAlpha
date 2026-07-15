"""CLI: truncate all ProspectAlpha business tables and reseed reference data.

Usage:
    cd backend && PYTHONPATH=src python -m scripts.reset_database --yes

Tables are cleared in reverse foreign-key order so child rows vanish before
their parents. All primary keys are String UUIDs, so no sequence reset is
needed. After the truncate the ``seed_reference_data`` helper re-populates
``agent_experts`` / ``agent_specs`` / ``risk_rule_sets`` / ``risk_rules`` /
``risk_rule_dimensions`` from the in-tree seed constants.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Type

from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from riskagent.database import session_factory_from_url
from riskagent.models import (
    AgentExpertRow,
    AgentSpecRow,
    AnalysisRunRow,
    DocumentAssetRow,
    DocumentBlockRow,
    EvidenceLibraryRow,
    RiskRuleDimensionRow,
    RiskRuleRow,
    RiskRuleSetRow,
)
from riskagent.seed_data import seed_reference_data

# Reverse-FK deletion order: leaf tables first, root reference tables last.
TABLES_IN_RESET_ORDER: list[Type[DeclarativeBase]] = [
    EvidenceLibraryRow,     # -> analysis_runs
    DocumentBlockRow,       # -> documents
    AnalysisRunRow,         # -> documents
    DocumentAssetRow,       # root business asset
    AgentSpecRow,           # reference (will be rebuilt by seed)
    AgentExpertRow,         # reference (will be rebuilt by seed)
    RiskRuleRow,            # -> risk_rule_sets, risk_rule_dimensions
    RiskRuleDimensionRow,   # -> risk_rule_sets
    RiskRuleSetRow,         # root reference
]


def reset_database(database_url: str) -> dict[str, int]:
    """Delete every row from the current business tables, then reseed reference data.

    Returns a mapping of ``{table_name: deleted_row_count}`` for observability.
    """
    session_factory = session_factory_from_url(database_url)
    deleted: dict[str, int] = {}
    with session_factory() as session:
        for row_cls in TABLES_IN_RESET_ORDER:
            table_name = row_cls.__tablename__
            result = session.execute(text(f"DELETE FROM {table_name}"))
            deleted[table_name] = result.rowcount or 0
        session.commit()
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
            f"This will DELETE all rows in {len(TABLES_IN_RESET_ORDER)} tables at {db_url}. "
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
