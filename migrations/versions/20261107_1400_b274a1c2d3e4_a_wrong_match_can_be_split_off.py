"""a wrong match can be split off (task B-274)

`resolution_matches` gains `split_at`, `split_by` and `split_entity_id`: a person split the
mention back off into a node of its own, and the node it became. All three or none, by CHECK.

Revision ID: b274a1c2d3e4
Revises: b273a1c2d3e4
Create Date: 2026-11-07 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b274a1c2d3e4"
down_revision: str | None = "b273a1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TOGETHER = (
    "(split_at IS NULL) = (split_by IS NULL) AND (split_at IS NULL) = (split_entity_id IS NULL)"
)


def upgrade() -> None:
    op.add_column(
        "resolution_matches", sa.Column("split_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("resolution_matches", sa.Column("split_by", sa.Text(), nullable=True))
    op.add_column(
        "resolution_matches", sa.Column("split_entity_id", sa.BigInteger(), nullable=True)
    )
    op.create_check_constraint("match_split_together", "resolution_matches", TOGETHER)


def downgrade() -> None:
    op.drop_constraint("match_split_together", "resolution_matches", type_="check")
    op.drop_column("resolution_matches", "split_entity_id")
    op.drop_column("resolution_matches", "split_by")
    op.drop_column("resolution_matches", "split_at")
