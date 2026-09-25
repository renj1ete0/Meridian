"""areas are named by field of work (task B-74)

Nullable and without a default: an area built before this has no field until
`worker.areas --name-only` names it, and reads fall back to its terms.

Revision ID: 6727563a22c2
Revises: 740a4f79888c
Create Date: 2026-09-25 19:35:33.328700
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6727563a22c2"
down_revision: str | None = "740a4f79888c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("areas", sa.Column("field", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("areas", "field")
