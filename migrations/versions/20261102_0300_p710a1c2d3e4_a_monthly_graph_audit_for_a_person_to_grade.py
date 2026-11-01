"""a monthly graph audit for a person to grade (task P7-10)

Two tables: `graph_audit_draws` (one reproducible sample over a window) and
`graph_audit_samples` (each sampled relation or merge, what the grader was shown, and the
grade). A `graph.audit` permission joins the catalogue, so the grants' CHECKs are rewritten
with it (written out here, as a migration must not change when the code does; a test compares
the newest copy with the code). And the timetable row for `worker.graphaudit`, for databases
seeded before it; `ON CONFLICT DO NOTHING` leaves a row the operator already made alone.

Revision ID: p710a1c2d3e4
Revises: p701a1c2d3e4
Create Date: 2026-11-02 03:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p710a1c2d3e4"
down_revision: str | None = "p701a1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_PERMISSIONS = (
    "explore.read",
    "admin.view",
    "work.write",
    "chat.ask",
    "topics.steer",
    "seeds.add",
    "gazetteer.curate",
    "duplicates.decide",
    "fetch_policy.edit",
    "budget.edit",
    "models.edit",
    "settings.edit",
    "access.manage",
)
PERMISSIONS = (*OLD_PERMISSIONS[:8], "graph.audit", *OLD_PERMISSIONS[8:])

#: Monthly: §14.1 reads "30 random edges monthly".
JOB: tuple[str, str, list[str] | None, int] = ("graph-audit", "worker.graphaudit", None, 2592000)


def _set_permission_checks(names: tuple[str, ...]) -> None:
    listed = ", ".join(f"'{name}'" for name in names)
    for column in ("allow", "deny"):
        op.drop_constraint(f"grant_{column}_known", "grants", type_="check")
        op.create_check_constraint(
            f"grant_{column}_known", "grants", f"{column} <@ ARRAY[{listed}]::text[]"
        )


def upgrade() -> None:
    _set_permission_checks(PERMISSIONS)
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

    op.create_table(
        "graph_audit_draws",
        sa.Column("draw_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("seed", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edge_population", sa.Integer(), nullable=False),
        sa.Column("merge_population", sa.Integer(), nullable=False),
        sa.Column("merges_reversed", sa.Integer(), nullable=False),
        sa.Column("drawn_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "edge_population >= 0 AND merge_population >= 0 AND merges_reversed >= 0",
            name=op.f("ck_graph_audit_draws_audit_counts_not_negative"),
        ),
        sa.CheckConstraint(
            "window_end > window_start", name=op.f("ck_graph_audit_draws_audit_window_ordered")
        ),
        sa.PrimaryKeyConstraint("draw_id", name=op.f("pk_graph_audit_draws")),
        sa.UniqueConstraint("window_start", name=op.f("uq_graph_audit_draws_window_start")),
    )
    op.create_index(
        op.f("ix_graph_audit_draws_created_at"), "graph_audit_draws", ["created_at"], unique=False
    )
    op.create_table(
        "graph_audit_samples",
        sa.Column("sample_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("draw_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("edge", "merge", name="audit_kind", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("shown", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("produced_by", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("quality_tier", sa.Integer(), nullable=True),
        sa.Column(
            "grade",
            sa.Enum(
                "correct",
                "wrong",
                "unsure",
                name="audit_grade",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("graded_by", sa.Text(), nullable=True),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(grade IS NULL) = (graded_at IS NULL) AND (grade IS NULL) = (graded_by IS NULL)",
            name=op.f("ck_graph_audit_samples_audit_graded_together"),
        ),
        sa.ForeignKeyConstraint(
            ["draw_id"],
            ["graph_audit_draws.draw_id"],
            name=op.f("fk_graph_audit_samples_draw_id_graph_audit_draws"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("sample_id", name=op.f("pk_graph_audit_samples")),
        sa.UniqueConstraint("draw_id", "kind", "item_id", name="uq_audit_sample_item"),
    )
    op.create_index(
        op.f("ix_graph_audit_samples_created_at"),
        "graph_audit_samples",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_graph_audit_samples_draw_id"), "graph_audit_samples", ["draw_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_graph_audit_samples_draw_id"), table_name="graph_audit_samples")
    op.drop_index(op.f("ix_graph_audit_samples_created_at"), table_name="graph_audit_samples")
    op.drop_table("graph_audit_samples")
    op.drop_index(op.f("ix_graph_audit_draws_created_at"), table_name="graph_audit_draws")
    op.drop_table("graph_audit_draws")
    name, module, _, _ = JOB
    op.execute(
        sa.text("DELETE FROM scheduled_jobs WHERE name = :name AND module = :module").bindparams(
            name=name, module=module
        )
    )
    # A switch naming the permission means nothing before this revision; it goes rather
    # than failing the CHECK.
    op.execute("UPDATE grants SET allow = array_remove(allow, 'graph.audit')")
    op.execute("UPDATE grants SET deny = array_remove(deny, 'graph.audit')")
    _set_permission_checks(OLD_PERMISSIONS)
