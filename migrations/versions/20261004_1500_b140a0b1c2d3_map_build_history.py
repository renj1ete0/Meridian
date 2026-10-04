"""the map's size is kept after its builds are pruned (task B-140, ADR 0005)

Revision ID: b140a0b1c2d3
Revises: b136ba1f0000
Create Date: 2026-10-04 15:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b140a0b1c2d3"
down_revision: str | None = "b136ba1f0000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "area_build_history",
        sa.Column("build_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("passages", sa.Integer(), nullable=False),
        sa.Column("regions", sa.Integer(), nullable=False),
        sa.Column("areas", sa.Integer(), nullable=False),
        sa.Column("sub_areas", sa.Integer(), nullable=False),
        sa.Column("weak_areas", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("build_id", name=op.f("pk_area_build_history")),
    )
    # The builds that still exist are the start of the history.
    op.execute(
        """
        INSERT INTO area_build_history
            (build_id, computed_at, passages, regions, areas, sub_areas, weak_areas)
        SELECT b.build_id, b.computed_at, b.passages,
               count(*) FILTER (WHERE a.level = 1),
               count(*) FILTER (WHERE a.level = 2),
               count(*) FILTER (WHERE a.level = 3),
               count(*) FILTER (WHERE a.level = 2 AND a.sources < 3)
          FROM area_builds b JOIN areas a ON a.build_id = b.build_id
         GROUP BY b.build_id
        """
    )


def downgrade() -> None:
    op.drop_table("area_build_history")
