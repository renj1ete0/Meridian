"""documents handed in through a connector (task B-264)

An `uploads` table; `upload` as a task type (`queue.task_type`) and as a seed source in both
columns that hold one (`queue.seed_source`, `fetch_policy.first_seen_via`); and a partial
index for the connector's list of DOIs no provider had a copy of. Autogenerate does not see
a widened CHECK, so all three are rewritten by hand; `upload` fits every column's width.

Revision ID: b264a1c2d3e4
Revises: b233a1c2d3e4
Create Date: 2026-11-01 23:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b264a1c2d3e4"
down_revision: str | None = "b233a1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = ("url", "query", "doi", "sitemap")
NEW_TYPES = (*OLD_TYPES, "upload")

OLD_SOURCES = (
    "frontier",
    "sitemap",
    "search",
    "citation",
    "doi",
    "model",
    "user",
    "diversity",
    "watch",
)
NEW_SOURCES = (*OLD_SOURCES, "upload")

#: Table and column of each seed-source CHECK; the bare name expands to ck_<table>_seed_source.
SOURCE_COLUMNS = (("queue", "seed_source"), ("fetch_policy", "first_seen_via"))

NO_COPY_WHERE = "task_type = 'doi' AND status = 'done' AND error = 'no_open_access_copy'"


def _check(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _set_checks(types: tuple[str, ...], sources: tuple[str, ...]) -> None:
    op.drop_constraint("task_type", "queue", type_="check")
    op.create_check_constraint("task_type", "queue", _check("task_type", types))
    for table, column in SOURCE_COLUMNS:
        op.drop_constraint("seed_source", table, type_="check")
        op.create_check_constraint("seed_source", table, _check(column, sources))


def upgrade() -> None:
    _set_checks(NEW_TYPES, NEW_SOURCES)

    op.create_table(
        "uploads",
        sa.Column("upload_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("inbox_path", sa.Text(), nullable=False),
        sa.Column("doi", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_tier", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "done",
                "failed",
                name="upload_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("task_id", sa.BigInteger(), nullable=True),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("client_address", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_uploads_sha256_is_hex")),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.source_id"],
            name=op.f("fk_uploads_source_id_sources"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["queue.task_id"],
            name=op.f("fk_uploads_task_id_queue"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("upload_id", name=op.f("pk_uploads")),
        sa.UniqueConstraint("sha256", name=op.f("uq_uploads_sha256")),
    )
    op.create_index(op.f("ix_uploads_created_at"), "uploads", ["created_at"], unique=False)
    op.create_index(op.f("ix_uploads_doi"), "uploads", ["doi"], unique=False)
    op.create_index(op.f("ix_uploads_source_id"), "uploads", ["source_id"], unique=False)

    op.create_index(
        "ix_queue_no_copy_dois",
        "queue",
        ["task_id"],
        unique=False,
        postgresql_where=sa.text(NO_COPY_WHERE),
    )


def downgrade() -> None:
    op.drop_index("ix_queue_no_copy_dois", table_name="queue", postgresql_where=NO_COPY_WHERE)
    op.drop_table("uploads")
    # An upload task means nothing before this revision; it goes rather than failing the CHECK.
    op.execute("DELETE FROM queue WHERE task_type = 'upload' OR seed_source = 'upload'")
    op.execute("UPDATE fetch_policy SET first_seen_via = 'user' WHERE first_seen_via = 'upload'")
    _set_checks(OLD_TYPES, OLD_SOURCES)
