"""a merge you can undo exactly

Task P4-03, §5.5. "Merges must be reversible. Reassign edges to the canonical
node, retain the old ID as a redirect rather than deleting, log every merge.
Bad merges are worse than duplicates because conflation is invisible once
done."

`entities.merged_from` and `redirects_to` have existed since `P0-07` and are
half the answer: they say *that* an entity was absorbed. They cannot say which
edges came with it — so reversing one of two merges into the same target would
take rows belonging to the other. This table records the ids actually moved,
which makes a reversal exact rather than approximate.

**Not foreign keys.** A log that disappears when the row it describes does
cannot answer questions about rows that disappeared, and those are the
questions worth asking about a merge.

**`reversed_at` rather than deleting the row.** "Merged and then reversed" is a
different and more interesting fact than "never merged" — it is what tells you
a threshold is wrong, which is what `P7-10`'s sampling is for.

Revision ID: 6b85d1a408c2
Revises: 6abd2d23835d
Create Date: 2026-09-20 21:37:51.724408
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6b85d1a408c2"
down_revision: str | None = "6abd2d23835d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "merge_log",
        sa.Column("merge_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("source_entity_id", sa.BigInteger(), nullable=False),
        sa.Column("target_entity_id", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("moved_edge_ids", postgresql.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column("moved_attribute_value_ids", postgresql.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column("moved_observation_ids", postgresql.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_by", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("merge_id", name=op.f("pk_merge_log")),
    )
    op.create_index(op.f("ix_merge_log_at"), "merge_log", ["at"], unique=False)
    op.create_index(
        op.f("ix_merge_log_source_entity_id"), "merge_log", ["source_entity_id"], unique=False
    )
    op.create_index(
        op.f("ix_merge_log_target_entity_id"), "merge_log", ["target_entity_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_merge_log_target_entity_id"), table_name="merge_log")
    op.drop_index(op.f("ix_merge_log_source_entity_id"), table_name="merge_log")
    op.drop_index(op.f("ix_merge_log_at"), table_name="merge_log")
    op.drop_table("merge_log")
