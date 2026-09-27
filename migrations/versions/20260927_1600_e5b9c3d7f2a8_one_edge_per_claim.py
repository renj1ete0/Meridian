"""one edge row per claim, checked at commit (task B-60)

Revision ID: e5b9c3d7f2a8
Revises: d4a8f2c6e1b7
Create Date: 2026-09-27 16:00:00.000000

Unique on (from_node, relation_type, to_node), DEFERRABLE INITIALLY DEFERRED.
Deferred because a merge moves edges and then folds the duplicates the move
made, inside one transaction; a per-statement check refused the move. The live
graph had no duplicates when this was written; one that does fails here,
loudly, rather than choosing between them.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5b9c3d7f2a8"
down_revision: str | None = "d4a8f2c6e1b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_edges_claim",
        "edges",
        ["from_node", "relation_type", "to_node"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint("uq_edges_claim", "edges", type_="unique")
