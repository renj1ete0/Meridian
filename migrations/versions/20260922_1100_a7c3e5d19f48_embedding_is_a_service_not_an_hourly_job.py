"""embedding is a service, not an hourly job

Task B-25, §6.1, §13.1. The timetable ran `worker.embed --once` every hour and
the scheduler kills a subprocess at 1800 seconds. On a real crawl that is not
a cadence, it is a ceiling: the pass was cut off at exactly 30 minutes having
embedded 3,584 chunks of 17,340, booked its next attempt an hour out, and left
a backlog that no later window could clear either. `last_status='timeout'` was
the only trace, in a table nobody reads.

`worker.embed` without `--once` is a poll loop with a SIGTERM handler, and
`docs/handover.md` has always listed it as one of four long-running processes.
So it becomes a compose service, and this row is disabled — **two owners is
worse than one**: both would claim batches, both would load weights, and they
would compete for the same CPU the crawl is already using.

**A migration as well as the YAML**, for the reason recorded in the handover:
`scripts/seed.py` is insert-only and §13.1 makes the database authoritative
after first boot, so editing `config/schedule.yaml` fixes new installs and
leaves every existing deployment running the job that cannot finish.

**The row is disabled, not deleted.** It records a cadence somebody would
otherwise reinvent, and re-enabling it is how the pass runs on a deployment
that has no `embed` service.

Revision ID: a7c3e5d19f48
Revises: d2a5c8b1f470
Create Date: 2026-09-22 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a7c3e5d19f48"
down_revision: str | Sequence[str] | None = "d2a5c8b1f470"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Only the row this change is about, and only while it still looks like the
    # seeded one. An operator who changed the interval or the arguments has made
    # a decision about their own deployment, and a migration that overrode it
    # would be replacing a choice with a default.
    op.execute(
        """
        UPDATE scheduled_jobs
           SET enabled = false,
               claimed_by = NULL,
               claimed_until = NULL
         WHERE name = 'embed'
           AND module = 'worker.embed'
           AND enabled
        """
    )


def downgrade() -> None:
    # Back on, because a deployment rolling back has no `embed` service either
    # — leaving it disabled would stop embedding altogether, which is worse
    # than the hourly pass this replaced.
    op.execute(
        """
        UPDATE scheduled_jobs
           SET enabled = true
         WHERE name = 'embed' AND module = 'worker.embed'
        """
    )
