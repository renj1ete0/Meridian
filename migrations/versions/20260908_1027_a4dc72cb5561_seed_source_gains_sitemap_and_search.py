"""seed_source gains sitemap and search

`P1-28` shipped a sitemap handler that passes ``seed_source="sitemap"`` to
``enqueue()``, and the value was never added to the enum — so every sitemap
that parsed successfully raised ``LookupError`` at the insert and queued
nothing. The feature has never enqueued a single URL in production.

This is `P0-21`'s trap for the third time: Alembic autogenerate does not detect
CheckConstraints on an existing table, so a value set can be widened in the
model and silently not in the database. Here it is worse — the value was never
in the model either, and only the code that *used* it knew about it. Hand-written
below, and `tests/integration/test_worker_run.py` now drives a sitemap through
the real loop, which is the test whose absence let this ship.

``search`` lands in the same migration because `P1-34`'s query handler needs it
and it is the same one-line constraint: a URL that came from a search ranking is
not a URL somebody linked to.

No ``alter_column``. The column is VARCHAR(9) because "diversity" is the longest
value, and it still is.

Revision ID: a4dc72cb5561
Revises: 7e8b0034d0f1
Create Date: 2026-09-08 10:27:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a4dc72cb5561"
down_revision: str | None = "7e8b0034d0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The bare name: the metadata naming convention expands it to
# ck_queue_seed_source on both create and drop.
CONSTRAINT = "seed_source"

OLD_SOURCES = ("frontier", "model", "user", "diversity")
NEW_SOURCES = ("frontier", "sitemap", "search", "model", "user", "diversity")


def _values(sources: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in sources)


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "queue", type_="check")
    op.create_check_constraint(CONSTRAINT, "queue", f"seed_source IN ({_values(NEW_SOURCES)})")


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "queue", type_="check")

    # Rows carrying a since-removed provenance would violate the narrower
    # constraint. Their provenance is a real fact about how they entered the
    # queue, so they are folded into the bucket they would have used before
    # this migration rather than deleted — the URL is still worth fetching,
    # only the answer to "how did it get here" is coarser.
    op.execute(
        "UPDATE queue SET seed_source = 'frontier' "
        f"WHERE seed_source NOT IN ({_values(OLD_SOURCES)})"
    )

    op.create_check_constraint(CONSTRAINT, "queue", f"seed_source IN ({_values(OLD_SOURCES)})")
