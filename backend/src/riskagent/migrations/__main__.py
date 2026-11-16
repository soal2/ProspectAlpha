"""Explicit migration commands.

    python -m riskagent.migrations upgrade
    python -m riskagent.migrations stamp-baseline
"""

from __future__ import annotations

import argparse

from riskagent.migrations import stamp_baseline, upgrade_database


def main() -> None:
    parser = argparse.ArgumentParser(description="升级 ProspectAlpha 数据库，不删除已有数据。")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("upgrade", help="把 ORM 和已有 RAG 表升到 head。")
    subcommands.add_parser(
        "stamp-baseline",
        help="仅在表结构已经符合基线时写入修订号。不会删除数据。",
    )
    args = parser.parse_args()
    if args.command == "upgrade":
        print(upgrade_database())
        return
    print(stamp_baseline(confirm=True))


if __name__ == "__main__":
    main()
