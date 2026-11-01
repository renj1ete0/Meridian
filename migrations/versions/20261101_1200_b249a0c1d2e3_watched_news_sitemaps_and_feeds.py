"""watched news sitemaps and feeds (task B-249)

A `watches` table, the `watch` job that reads them (inserted here as well as seeded
from config/schedule.yaml, which reaches a new database only), and `watch` as a seed
source in both columns that hold one (`queue.seed_source`, `fetch_policy.first_seen_via`).
Autogenerate does not see a widened CHECK, so both are rewritten by hand; `watch` fits the
VARCHAR(9) the longest value ("diversity") set.

Revision ID: b249a0c1d2e3
Revises: b217c1a2d3e4
Create Date: 2026-11-01 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b249a0c1d2e3"
down_revision: str | None = "b217c1a2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_SOURCES = ("frontier", "sitemap", "search", "citation", "doi", "model", "user", "diversity")
NEW_SOURCES = (*OLD_SOURCES, "watch")

#: Table and column of each seed-source CHECK; the bare name expands to ck_<table>_seed_source.
COLUMNS = (("queue", "seed_source"), ("fetch_policy", "first_seen_via"))

JOB = ("watch", "worker.watch", ["--apply"], 300)


def _check(column: str, sources: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(s) for s in sources)})"


def upgrade() -> None:
    for table, column in COLUMNS:
        op.drop_constraint("seed_source", table, type_="check")
        op.create_check_constraint("seed_source", table, _check(column, NEW_SOURCES))

    op.create_table(
        "watches",
        sa.Column("watch_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "sitemap",
                "feed",
                name="watch_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("headline_filter", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), server_default=sa.text("900"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("interval_seconds >= 300", name=op.f("ck_watches_watch_interval_floor")),
        sa.CheckConstraint(
            "topic IS NOT NULL OR headline_filter", name=op.f("ck_watches_watch_topic_or_filter")
        ),
        sa.PrimaryKeyConstraint("watch_id", name=op.f("pk_watches")),
        sa.UniqueConstraint("url", name=op.f("uq_watches_url")),
    )
    op.create_index(op.f("ix_watches_created_at"), "watches", ["created_at"], unique=False)

    name, module, args, interval = JOB
    op.execute(
        sa.text(
            """
            INSERT INTO scheduled_jobs (name, module, args, interval_seconds, enabled)
            VALUES (:name, :module, :args, :interval, true)
            ON CONFLICT (name) DO NOTHING
            """
        ).bindparams(name=name, module=module, args=args, interval=interval)
    )


def downgrade() -> None:
    name, module, args, _ = JOB
    op.execute(
        sa.text(
            "DELETE FROM scheduled_jobs WHERE name = :name AND module = :module AND args = :args"
        ).bindparams(name=name, module=module, args=args)
    )
    op.drop_table("watches")
    for table, column in COLUMNS:
        # A row queued by a watch keeps its provenance as the nearest older value.
        op.execute(f"UPDATE {table} SET {column} = 'sitemap' WHERE {column} = 'watch'")
        op.drop_constraint("seed_source", table, type_="check")
        op.create_check_constraint("seed_source", table, _check(column, OLD_SOURCES))
