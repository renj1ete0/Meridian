"""the robots cache survives a restart

Task P1-29. The cache was in-process, so a worker restart re-fetched
`/robots.txt` for every origin it touched. Harmless while the crawl is deep and
a handful of domains, and wasteful once it is wide: a restart costs one extra
request per domain, paid against the same rate limiter the pages queue behind,
so the first minutes back are spent not crawling.

The raw file is stored rather than the parsed rules. Re-parsing on load is
cheap, the compiled matchers are not serialisable in any form worth versioning,
and a parser fix then applies to everything already cached. The file is also the
evidence for "why was this URL refused".

`expires_at` is wall clock, which is the one thing here that is easy to get
wrong: the in-process cache expires on `time.monotonic()`, and a persisted
monotonic deadline would be compared against a different clock after exactly the
restart this table exists to survive.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from meridian_core.models.robots import ROBOTS_OUTCOME

revision: str = "f2b81c46d7e9"
down_revision: str | None = "e5c72b91f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "robots_cache",
        # The `/robots.txt` URL: the origin in the only form that matters, with
        # the scheme, because http and https may serve different files.
        sa.Column("origin", sa.Text(), primary_key=True),
        sa.Column("outcome", ROBOTS_OUTCOME, nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_robots_cache_expires_at", "robots_cache", ["expires_at"])
    # `TimestampMixin` indexes `created_at` on every table that uses it, so this
    # is not optional decoration — leaving it out is a migration that disagrees
    # with the model, which `alembic check` fails on.
    op.create_index("ix_robots_cache_created_at", "robots_cache", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_robots_cache_created_at", table_name="robots_cache")
    op.drop_index("ix_robots_cache_expires_at", table_name="robots_cache")
    # No enum type to drop after it: `constrained()` builds a VARCHAR with a
    # CHECK rather than a native enum, so the constraint goes with the table.
    op.drop_table("robots_cache")
