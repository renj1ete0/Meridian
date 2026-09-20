"""a run that died and a run that is working

Task P4-08, §11.10. Orchestrator state is a plain table and a crash mid-flight
resumes from the stage it reached. That only works if the next wake can tell a
run that is *working* from one that died holding the row — and from outside
they are identical: both are `status='running'` with a stage.

`heartbeat_at` is the difference. The orchestrator touches it as it completes
each step, so a stale one means nobody is there. NULL reads as stale, which is
the right answer for both the rows that exist before this migration and a run
that died before finishing its first step.

**Two unique partial indexes, because the invariant is not "one run".** It is
*at most one unfinished run*: two orchestrators on one corpus means double
spend against the monthly ceiling and two sets of writes racing the same
high-water mark, and §11.9's point is that the first signal of runaway cost
would be the bill. A unique index on `status` restricted to a single value
permits exactly one row holding it, which is the cheapest possible way to say
that — and it is the database saying it, so a second orchestrator started by
hand gets an error rather than a quiet second run.

`deferred` gets the same treatment (§13.4's "skip and retry next cycle"): a
deferred run is resumable state, and two of them would make the next wake
choose which day's work to continue.

**Existing duplicates are settled before the indexes exist**, or the migration
would fail on exactly the databases that most need it. The newest row of each
status is kept, because it is the one whose work is furthest along.

Revision ID: 70dbd9d6b14e
Revises: c1f4a0d7e9b2
Create Date: 2026-09-20 22:31:35.771794
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "70dbd9d6b14e"
down_revision: str | Sequence[str] | None = "c1f4a0d7e9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTLED = (
    "superseded by P4-08: more than one unfinished run existed, "
    "and at most one is now permitted"
)


def upgrade() -> None:
    op.add_column("runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))

    # Keep the newest of each unfinished status; fail the rest with a reason
    # rather than deleting them. A run that was interrupted is evidence about
    # what the orchestrator was doing, and §10.2's position — nothing is
    # destroyed — applies to its own history too.
    for status in ("running", "deferred"):
        op.execute(
            f"""
            UPDATE runs
               SET status = 'failed',
                   error = COALESCE(error || ' | ', '') || '{SETTLED}',
                   completed_at = COALESCE(completed_at, now())
             WHERE status = '{status}'
               AND run_id <> (SELECT max(run_id) FROM runs WHERE status = '{status}')
            """
        )

    op.create_index(
        "ix_runs_one_running",
        "runs",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "ix_runs_one_deferred",
        "runs",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'deferred'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_runs_one_running", table_name="runs", postgresql_where=sa.text("status = 'running'")
    )
    op.drop_index(
        "ix_runs_one_deferred", table_name="runs", postgresql_where=sa.text("status = 'deferred'")
    )
    op.drop_column("runs", "heartbeat_at")
    # The settled rows are not un-settled. Which of several unfinished runs was
    # the live one is not recorded, and guessing would put the orchestrator
    # back onto work another row already did.
