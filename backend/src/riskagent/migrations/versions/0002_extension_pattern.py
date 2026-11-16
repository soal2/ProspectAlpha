"""Document the forward-only extension point after the baseline.

Revision ID: 0002_extension_pattern
Revises: 0001_baseline
Create Date: 2026-11-16

This revision intentionally changes no business table. The next schema change,
including a versioned score snapshot from the other stream, should be a new
file in this directory:

    revision = "0003_score_snapshot_version"
    down_revision = "0002_extension_pattern"

    def upgrade() -> None:
        op.add_column(...)

    def downgrade() -> None:
        op.drop_column(...)

Do not add another startup ``create_all`` or hand-written ``ALTER`` path.
"""

revision = "0002_extension_pattern"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    return None


def downgrade() -> None:
    return None
