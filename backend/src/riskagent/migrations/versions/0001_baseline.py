"""Baseline ORM schema for existing and empty databases.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-16
"""

from riskagent.migrations.baseline import upgrade_baseline

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    upgrade_baseline(op_bind())


def downgrade() -> None:
    raise RuntimeError(
        "0001_baseline 的回滚会删除已有业务表，因此不受支持。"
        "后续变更请新增向前修订，不要降级到空版本。"
    )


def op_bind():
    from alembic import op

    return op.get_bind()
