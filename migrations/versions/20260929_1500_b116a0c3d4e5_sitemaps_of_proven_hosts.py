"""the sitemaps of proven hosts are mined hourly (task B-116)

Inserted here as well as seeded from config/schedule.yaml, because the config
seeds a database at first boot only and an existing one would never see a new
job.

Revision ID: b116a0c3d4e5
Revises: 7c2e4a91b6d3
Create Date: 2026-09-29 15:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b116a0c3d4e5"
down_revision: str | None = "7c2e4a91b6d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB = ("sitemapmine", "worker.sitemapmine", ["--apply"], 3600)


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
