"""seed_source gains citation and doi

`P1-14` adds two more answers to §5.2's "how did this URL get here": a work
another paper cited (`citation`, on the `doi` task the reference list produced)
and the open-access copy found by resolving it (`doi`, on the `url` task that
resolution produced).

Hand-written for the reason `P0-21` records and `a4dc72cb5561` hit again:
Alembic autogenerate does not detect CheckConstraints on an existing table, so
widening the model's value set here would leave the database rejecting every one
of the new values with nothing failing until the first insert.

No ``alter_column``: the column is VARCHAR(9) because "diversity" is the longest
value, and "citation" (8) and "doi" (3) do not change that.

Revision ID: 3d7b422a25e1
Revises: a4dc72cb5561
Create Date: 2026-09-08 11:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "3d7b422a25e1"
down_revision: str | None = "a4dc72cb5561"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The bare name: the metadata naming convention expands it to
# ck_queue_seed_source on both create and drop.
CONSTRAINT = "seed_source"

OLD_SOURCES = ("frontier", "sitemap", "search", "model", "user", "diversity")
NEW_SOURCES = ("frontier", "sitemap", "search", "citation", "doi", "model", "user", "diversity")


def _values(sources: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in sources)


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "queue", type_="check")
    op.create_check_constraint(CONSTRAINT, "queue", f"seed_source IN ({_values(NEW_SOURCES)})")


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "queue", type_="check")

    # Rows carrying a since-removed provenance would violate the narrower
    # constraint. The URL is still worth fetching; only the answer to "how did
    # it get here" becomes coarser, so they are folded into `frontier` rather
    # than deleted.
    op.execute(
        "UPDATE queue SET seed_source = 'frontier' "
        f"WHERE seed_source NOT IN ({_values(OLD_SOURCES)})"
    )

    op.create_check_constraint(CONSTRAINT, "queue", f"seed_source IN ({_values(OLD_SOURCES)})")
