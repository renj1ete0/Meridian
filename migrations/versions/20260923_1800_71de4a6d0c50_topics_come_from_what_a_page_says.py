"""topics come from what a page says, not from why it was crawled

Task P2-21. `sources.topic_labels` was written at fetch time from the queue
topic that caused the fetch, unioned with a URL-path match. That is provenance,
and a crawl pursuing one topic stamped it on every page a site's navigation led
to — clinical pages, privacy notices, court rules — whatever they were about.

Four columns on `sources` and one on `topic_config`:

- `crawled_for` — the provenance, under its own name. **Backfilled from
  `topic_labels`**, which is the best record of it that exists: every label
  written before this revision was the claim's topic plus a path match, and
  nothing kept the two apart, so a backfilled row may carry a path-matched
  topic the crawl was not in fact pursuing. Rows fetched from here on carry
  only the queue topic.
- `topics_examined_at`, `topic_basis`, `topic_scores` — what the content
  labeller (`python -m worker.retopic`) needs to know whether a label is
  current, and to explain it.
- `topic_config.description` — most of what a topic is compared against.

**`topic_labels` is reset to NULL.** Its old values were provenance and now
live in `crawled_for`; leaving them in place would have them read as content
labels until the labeller got round to each row, and NULL is the honest state
— nothing has examined these sources' content. Search with a topic filter
returns nothing until the labeller has run, and the landing page's
"not yet examined" count says why.

And the timetable row, because `scripts/seed.py` is insert-only and a database
seeded before this revision would otherwise never run the labeller. Inserted
only if absent: an operator who already added one made their own decision.
Without `--demote-offtopic`, always — a demotion hands sources to the retention
sweep and is a person's call, not a timer's.

Revision ID: 71de4a6d0c50
Revises: 525b10621a92
Create Date: 2026-09-23 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "71de4a6d0c50"
down_revision: str | Sequence[str] | None = "525b10621a92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("crawled_for", ARRAY(sa.Text()), nullable=True))
    op.add_column(
        "sources", sa.Column("topics_examined_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("sources", sa.Column("topic_basis", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("topic_scores", JSONB(), nullable=True))
    op.add_column("topic_config", sa.Column("description", sa.Text(), nullable=True))

    op.execute(
        """
        UPDATE sources
           SET crawled_for = topic_labels,
               topic_labels = NULL
         WHERE topic_labels IS NOT NULL
        """
    )

    op.execute(
        """
        INSERT INTO scheduled_jobs (name, module, args, interval_seconds, enabled)
        VALUES ('topics', 'worker.retopic', ARRAY['--apply'], 3600, true)
        ON CONFLICT (name) DO NOTHING
        """
    )


def downgrade() -> None:
    # Only the row this revision could have written, and only while it still
    # looks like the one it wrote.
    op.execute(
        """
        DELETE FROM scheduled_jobs
         WHERE name = 'topics' AND module = 'worker.retopic' AND args = ARRAY['--apply']
        """
    )
    # Back to the old meaning: provenance in `topic_labels`. Content labels
    # written since are discarded — the previous schema has nowhere to hold
    # them apart from provenance, which is the whole problem this revision fixed.
    op.execute(
        """
        UPDATE sources
           SET topic_labels = crawled_for
        """
    )
    op.drop_column("topic_config", "description")
    op.drop_column("sources", "topic_scores")
    op.drop_column("sources", "topic_basis")
    op.drop_column("sources", "topics_examined_at")
    op.drop_column("sources", "crawled_for")
