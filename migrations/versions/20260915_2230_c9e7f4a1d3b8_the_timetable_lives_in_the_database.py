"""the timetable lives in the database

Task P5-06. §13.1's corollary: "no cron files. The scheduler reads its timetable
from the DB so schedule changes are a UI action."

A crontab on the box is configuration nobody can see from the interface, cannot
change without SSH, and does not travel with a database snapshot — so a restored
corpus arrives with no idea what was supposed to be running.

`module` rather than a shell command, deliberately. A timetable row is editable
from a UI (§13.2), and a row that could name a shell string would make this
table a remote execution surface for anyone who could write to it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

from meridian_core.models.config import JOB_STATUS

revision: str = "c9e7f4a1d3b8"
down_revision: str | None = "b8d41f6e2c07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_jobs",
        sa.Column("job_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("module", sa.Text(), nullable=False),
        sa.Column("args", ARRAY(sa.Text()), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "next_run_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claimed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        # The `constrained()` type, not bare Text. `P0-21`: autogenerate does
        # not detect a CHECK on an existing table, so a mismatch here ships a
        # column that accepts anything and a model that pretends otherwise.
        sa.Column("last_status", JOB_STATUS, nullable=True),
        sa.Column("last_duration_ms", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # `TimestampMixin` declares this indexed on every table it is mixed into.
    op.create_index("ix_scheduled_jobs_created_at", "scheduled_jobs", ["created_at"])
    # The claim query: enabled jobs that are due, oldest first.
    op.create_index(
        "ix_scheduled_jobs_due",
        "scheduled_jobs",
        ["next_run_at"],
        postgresql_where=sa.text("enabled"),
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_jobs_due", table_name="scheduled_jobs")
    op.drop_index("ix_scheduled_jobs_created_at", table_name="scheduled_jobs")
    op.drop_table("scheduled_jobs")
