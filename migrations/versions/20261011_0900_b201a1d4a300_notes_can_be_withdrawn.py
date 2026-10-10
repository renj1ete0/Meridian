"""a note can be withdrawn, and keeps its record (task B-201)

Revision ID: b201a1d4a300
Revises: b155f011ed00
Create Date: 2026-10-11 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b201a1d4a300"
down_revision: str | None = "b155f011ed00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL is a live note; only notes are ever withdrawn. See ADR 0020.
    op.add_column("entities", sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("entities", "withdrawn_at")
