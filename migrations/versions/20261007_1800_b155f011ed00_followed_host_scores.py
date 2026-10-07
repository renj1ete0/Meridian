"""a host's record on the pages reached by following links (task B-155)

Revision ID: b155f011ed00
Revises: b150c0ffee01
Create Date: 2026-10-07 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b155f011ed00"
down_revision: str | None = "b150c0ffee01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Derived wholesale by `worker.hostscore` every hour; zero until its next run.
    op.add_column(
        "host_scores",
        sa.Column("followed_examined", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "host_scores",
        sa.Column("followed_on_topic", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    # `requeue` applies host verdicts to waiting links; with standings that now change as
    # followed pages are read, a day is too long to wait. Only from the old default, so an
    # operator's own interval stands.
    op.execute(
        "UPDATE scheduled_jobs SET interval_seconds = 3600 "
        "WHERE name = 'requeue' AND interval_seconds = 86400"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE scheduled_jobs SET interval_seconds = 86400 "
        "WHERE name = 'requeue' AND interval_seconds = 3600"
    )
    op.drop_column("host_scores", "followed_on_topic")
    op.drop_column("host_scores", "followed_examined")
