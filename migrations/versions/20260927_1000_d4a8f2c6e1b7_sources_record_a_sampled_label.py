"""a source can be labelled from a sample of its passages (task B-89)

Revision ID: d4a8f2c6e1b7
Revises: c7d2e9a1b4f3
Create Date: 2026-09-27 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a8f2c6e1b7"
down_revision: str | None = "c7d2e9a1b4f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("topic_sample_best", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "topic_sample_best")
