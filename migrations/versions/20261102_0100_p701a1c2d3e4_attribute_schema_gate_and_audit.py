"""the attribute schema's log, and the daily pass that audits and judges it (tasks P7-01, P7-02)

`attribute_events` records every proposal judged, every audit and every status change, with
the measurements behind it (§7.3 "all schema changes logged"). The timetable row runs
`worker.attributes --once`, for databases seeded before it: config seeds at first boot only, so
a row added to `config/schedule.yaml` reaches an existing deployment only through a migration.
`ON CONFLICT DO NOTHING` leaves a row the operator already made alone.

Revision ID: p701a1c2d3e4
Revises: c705a1b2c3d4
Create Date: 2026-11-02 01:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p701a1c2d3e4"
down_revision: str | None = "c705a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB = ("attributes", "worker.attributes", ["--once"], 86400)


def upgrade() -> None:
    op.create_table(
        "attribute_events",
        sa.Column("event_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("attribute_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "proposed",
                "refused",
                "held",
                "activated",
                "audited",
                "retired",
                "restored",
                name="attribute_event",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "signals",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["attribute_id"],
            ["attribute_definitions.attribute_id"],
            name=op.f("fk_attribute_events_attribute_id_attribute_definitions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.run_id"],
            name=op.f("fk_attribute_events_run_id_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_attribute_events")),
    )
    op.create_index(
        "ix_attribute_events_attribute",
        "attribute_events",
        ["attribute_id", "event_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_attribute_events_created_at"), "attribute_events", ["created_at"], unique=False
    )
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
    op.drop_index(op.f("ix_attribute_events_created_at"), table_name="attribute_events")
    op.drop_index("ix_attribute_events_attribute", table_name="attribute_events")
    op.drop_table("attribute_events")
