"""document duplicates on the timetable, disabled

Revision ID: 4870251e4296
Revises: 7fbdd2242063
Create Date: 2026-09-24 21:26:06.775051
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "4870251e4296"
down_revision: str | None = "7fbdd2242063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO scheduled_jobs (name, module, args, interval_seconds, enabled)
        VALUES ('docdupes', 'worker.docdupes', ARRAY['--apply'], 86400, false)
        ON CONFLICT (name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM scheduled_jobs
         WHERE name = 'docdupes' AND module = 'worker.docdupes' AND args = ARRAY['--apply']
        """
    )
