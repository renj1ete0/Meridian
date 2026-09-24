"""topics per passage (task P2-24)

`chunk_topics` holds each passage's own topic labels, keyed by chunk. A side
table rather than columns on `chunks`, so that re-labelling after a topic
change does not rewrite (and re-index) every vector row. No timetable row: the
hourly `topics` job (`worker.retopic --apply`) runs the passage pass as its
second stage.

Revision ID: baa1aa041c08
Revises: b41c0b1ed0a4
Create Date: 2026-09-25 01:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "baa1aa041c08"
down_revision: str | None = "b41c0b1ed0a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunk_topics",
        sa.Column("chunk_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_labels", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("topic_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("topic_basis", sa.Text(), nullable=False),
        sa.Column("embedding_view", sa.SmallInteger(), nullable=True),
        sa.Column(
            "topics_examined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.chunk_id"],
            name=op.f("fk_chunk_topics_chunk_id_chunks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chunk_id", name=op.f("pk_chunk_topics")),
    )
    op.create_index(
        "ix_chunk_topics_labels",
        "chunk_topics",
        ["topic_labels"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_chunk_topics_labels", table_name="chunk_topics", postgresql_using="gin")
    op.drop_table("chunk_topics")
