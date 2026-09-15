"""sources record their topics

Task P2-14. §12.5 lists topic among the search filters and §12.3 lists it among
the canvas filters, and `sources` had no such column — so the one dimension the
whole system is organised around (steering weights topics, coverage scores
topics, seeds are drawn per topic) was the one thing a reader could not filter
by.

The crawl already knew. The topic is on the queue row that produced the fetch,
and `upsert_source` dropped it.

An array rather than a single value, and named `topic_labels` to match
`entities` and `gazetteer`, which already carry exactly this. A source genuinely
belongs to more than one topic: a URL can be enqueued under several, and the
path itself can match several more. Picking one would make the label depend on
whichever crawl ran last.

Filtering through a join back to `queue` on the URL was the alternative and is
worse than offering nothing: a URL can be enqueued repeatedly under different
topics, and a redirect means the fetched URL is frequently not the queued one.

NULL rather than `{}` on the rows that already exist. "Never examined" and
"examined, matched nothing" are different facts, and only the first is worth a
backfill pass — `python -m worker.retopic`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "b7e4c1a92f38"
down_revision: str | None = "a9d3e71b58c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("topic_labels", ARRAY(sa.Text()), nullable=True))
    # GIN, because the query is array overlap (`&&`) rather than equality. A
    # btree cannot answer it at all, so without this the topic filter is a
    # sequential scan over every source in the corpus.
    op.create_index(
        "ix_sources_topic_labels",
        "sources",
        ["topic_labels"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_sources_topic_labels", table_name="sources")
    op.drop_column("sources", "topic_labels")
