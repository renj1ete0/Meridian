"""the harvest's gazetteer lookups are indexed (task B-85)

Revision ID: c7d2e9a1b4f3
Revises: b3e1c4d2a9f0
Create Date: 2026-09-26 21:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d2e9a1b4f3"
down_revision: str | None = "b3e1c4d2a9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_gazetteer_canonical_lower", "gazetteer", [sa.text("lower(canonical)")])
    op.create_index("ix_gazetteer_aliases", "gazetteer", ["aliases"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_gazetteer_aliases", table_name="gazetteer")
    op.drop_index("ix_gazetteer_canonical_lower", table_name="gazetteer")
