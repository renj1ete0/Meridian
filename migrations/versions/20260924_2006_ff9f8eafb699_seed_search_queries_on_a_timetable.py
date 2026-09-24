"""seed search queries on a timetable (task B-51)

Data only: the timetable row for `worker.seedsearch`, since `scripts/seed.py` is
insert-only and a database seeded before this revision would never run it.

Revision ID: ff9f8eafb699
Revises: 68fd9387a022
Create Date: 2026-09-24 20:06:28.084878
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "ff9f8eafb699"
down_revision: str | None = "68fd9387a022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO scheduled_jobs (name, module, args, interval_seconds, enabled)
        VALUES ('seedsearch', 'worker.seedsearch', ARRAY['--once'], 21600, true)
        ON CONFLICT (name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM scheduled_jobs
         WHERE name = 'seedsearch' AND module = 'worker.seedsearch' AND args = ARRAY['--once']
        """
    )
