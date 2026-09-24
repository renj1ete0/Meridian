"""a merge folds a repeated claim into the one already held (task B-41)

Revision ID: b41c0b1ed0a4
Revises: f5b9328b0df8
Create Date: 2026-09-25 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b41c0b1ed0a4"
down_revision: str | None = "f5b9328b0df8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "merge_log",
        sa.Column("moved_columns", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "merge_log",
        sa.Column("combined", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "merge_log",
        sa.Column("target_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("merge_log", "target_fields")
    op.drop_column("merge_log", "combined")
    op.drop_column("merge_log", "moved_columns")
