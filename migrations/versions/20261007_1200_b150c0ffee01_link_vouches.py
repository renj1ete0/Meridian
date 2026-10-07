"""which pages link to which other hosts, and how many on-topic sites vouch for a host (task B-150)

Revision ID: b150c0ffee01
Revises: b140a0b1c2d3
Create Date: 2026-10-07 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b150c0ffee01"
down_revision: str | None = "b140a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "link_vouches",
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.source_id"],
            name=op.f("fk_link_vouches_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("host", "source_id", name=op.f("pk_link_vouches")),
    )
    op.create_index(op.f("ix_link_vouches_source_id"), "link_vouches", ["source_id"])
    op.add_column(
        "host_scores",
        sa.Column("vouched", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    # The first parent of every queued link is the evidence that already exists: one vouch per
    # (linked host, linking page) on another host. Hosts are lower-cased without a leading
    # `www.`, as `boilerplate.host_key` writes them.
    op.execute(
        """
        INSERT INTO link_vouches (host, source_id)
        SELECT DISTINCT t.host, q.parent_source_id
          FROM queue q
          JOIN sources s ON s.source_id = q.parent_source_id
          CROSS JOIN LATERAL (
            SELECT regexp_replace(lower(split_part(split_part(split_part(
                     q.url_or_query, '://', 2), '/', 1), ':', 1)), '^www\\.', '') AS host,
                   regexp_replace(lower(split_part(split_part(split_part(
                     s.url, '://', 2), '/', 1), ':', 1)), '^www\\.', '') AS parent_host
          ) t
         WHERE q.task_type = 'url' AND q.parent_source_id IS NOT NULL
           AND t.host <> '' AND t.host <> t.parent_host
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_column("host_scores", "vouched")
    op.drop_index(op.f("ix_link_vouches_source_id"), table_name="link_vouches")
    op.drop_table("link_vouches")
