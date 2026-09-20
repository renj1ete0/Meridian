"""a note records what it was drawn from

Task P6-05. §12.5: "Annotation as first-class nodes — my own notes and edges,
tagged as mine."

An annotation is an ``entities`` row, and every column it needs was already
there but one. §2 principle 3 requires an *edge*, tag or attribute to name the
chunks that justify it, and says nothing about nodes — correctly, because a
derived node is justified by the edges and attribute values that cite it, each
carrying their own chunks.

A node somebody wrote by hand has none of those. The note is the claim, so the
passages they were reading while writing it have nowhere to live, and without
this column annotation would only work once the graph has nodes to attach to —
which is phase 4, and §12.5's whole point is that the affordance has to exist
before that or the habit never forms.

Empty for every row the pipeline writes. The note's own `annotates` edges carry
the same ids rather than `{}`: every edge in this schema names the chunks behind
it, and an edge exempted from that because the node beside it happened to record
them would be the one edge a traversal could not explain. One writer owns both,
so they cannot drift.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "e6c84b2d719f"
down_revision: str | None = "d4a92c78b6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOT NULL with a default rather than nullable: an empty array and "nobody
    # recorded one" are the same fact here, and two ways to spell it is two
    # branches in every reader.
    op.add_column(
        "entities",
        sa.Column(
            "supporting_chunk_ids",
            ARRAY(sa.BigInteger()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("entities", "supporting_chunk_ids")
