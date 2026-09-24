"""which places a source is about (task P2-23)

Four columns on `sources` — `places`, `places_examined_at`, `place_basis`,
`place_evidence` — and a GIN index, because the filter is array overlap.

All start NULL, which is the truth: nothing has examined any source for places.
`python -m worker.places` fills them.

And the timetable row, because `scripts/seed.py` is insert-only and a database
seeded before this revision would otherwise never run the pass. Inserted only
if absent: an operator who already added one made their own decision.

Revision ID: b3f1c7d2e8a4
Revises: 9c34c91de722
Create Date: 2026-09-25 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "b3f1c7d2e8a4"
down_revision: str | None = "9c34c91de722"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("places", ARRAY(sa.Text()), nullable=True))
    op.add_column(
        "sources", sa.Column("places_examined_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("sources", sa.Column("place_basis", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("place_evidence", JSONB(), nullable=True))
    op.create_index(
        "ix_sources_places", "sources", ["places"], unique=False, postgresql_using="gin"
    )

    op.execute(
        """
        INSERT INTO scheduled_jobs (name, module, args, interval_seconds, enabled)
        VALUES ('places', 'worker.places', ARRAY['--apply'], 3600, true)
        ON CONFLICT (name) DO NOTHING
        """
    )


def downgrade() -> None:
    # Only the row this revision could have written, and only while it still
    # looks like the one it wrote.
    op.execute(
        """
        DELETE FROM scheduled_jobs
         WHERE name = 'places' AND module = 'worker.places' AND args = ARRAY['--apply']
        """
    )
    op.drop_index("ix_sources_places", table_name="sources", postgresql_using="gin")
    op.drop_column("sources", "place_evidence")
    op.drop_column("sources", "place_basis")
    op.drop_column("sources", "places_examined_at")
    op.drop_column("sources", "places")
