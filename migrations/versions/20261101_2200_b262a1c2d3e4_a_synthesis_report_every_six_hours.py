"""a synthesis report every six hours (task B-262)

The timetable row for `worker.digest --runs`, for databases seeded before it: config seeds at
first boot only, so a row added to `config/schedule.yaml` reaches an existing deployment only
through a migration. `ON CONFLICT DO NOTHING` leaves a row the operator already made alone.

Revision ID: b262a1c2d3e4
Revises: b259a1c2d3e4
Create Date: 2026-11-01 22:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b262a1c2d3e4"
down_revision: str | None = "b259a1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB = ("runs-report", "worker.digest", ["--runs"], 21600)


def upgrade() -> None:
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
