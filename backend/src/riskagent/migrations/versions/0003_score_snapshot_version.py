"""Append immutable score snapshot columns.

Revision ID: 0003_score_snapshot_version
Revises: 0002_extension_pattern
Create Date: 2026-11-16

Adds the content hash used by append-only score rows and the pinned score
payload copied onto a replay candidate. Existing rows stay. Empty content_hash
means the historic row was written before this revision.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_score_snapshot_version"
down_revision = "0002_extension_pattern"
branch_labels = None
depends_on = None


def _has_column(table: str, name: str) -> bool:
    bind = op.get_bind()
    return any(column["name"] == name for column in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    if not _has_column("risk_score_snapshots", "content_hash"):
        op.add_column(
            "risk_score_snapshots",
            sa.Column("content_hash", sa.String(length=64), nullable=False, server_default=""),
        )
    if not _has_column("backtest_candidate_samples", "pinned_score"):
        op.add_column(
            "backtest_candidate_samples",
            sa.Column("pinned_score", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("backtest_candidate_samples", "pinned_score"):
        op.drop_column("backtest_candidate_samples", "pinned_score")
    if _has_column("risk_score_snapshots", "content_hash"):
        op.drop_column("risk_score_snapshots", "content_hash")
