"""an assertion against a claim is its own edge, marked contested (task P7-05)

`edges.opposed` is generated from the stance (`opposes` argues against the claim) and joins
the claim's key, so the same subject, relation and object can be held once for and once
against. The two rows are marked contested with each other by the write path; nothing here
marks, since no claim was held twice before this.

Revision ID: c705a1b2c3d4
Revises: b247a1c2d3e4
Create Date: 2026-11-02 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c705a1b2c3d4"
down_revision: str | None = "b247a1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLAIM = ["from_node", "relation_type", "to_node"]


def upgrade() -> None:
    op.add_column(
        "edges",
        sa.Column(
            "opposed",
            sa.Boolean(),
            sa.Computed("coalesce(stance = 'opposes', false)", persisted=True),
            nullable=False,
        ),
    )
    op.drop_constraint("uq_edges_claim", "edges", type_="unique")
    op.create_unique_constraint(
        "uq_edges_claim",
        "edges",
        [*CLAIM, "opposed"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    held_twice = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM (SELECT 1 FROM edges "
                "GROUP BY from_node, relation_type, to_node HAVING count(*) > 1) AS twice"
            )
        )
        .scalar()
    )
    if held_twice:
        # Folding one side into the other would resolve the contradiction (§9).
        raise RuntimeError(
            f"{held_twice} claim(s) are held both for and against; the old key cannot "
            "hold them. Delete one side of each by hand before downgrading."
        )
    op.drop_constraint("uq_edges_claim", "edges", type_="unique")
    op.create_unique_constraint(
        "uq_edges_claim", "edges", CLAIM, deferrable=True, initially="DEFERRED"
    )
    op.drop_column("edges", "opposed")
