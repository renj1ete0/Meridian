"""listing pages as watches (task B-255)

A third watch kind, `listing`: an outlet's latest-articles page, read for its article links
when the outlet publishes no news sitemap or feed. The CHECK is rewritten by hand, as
autogenerate does not see CHECKs on existing tables; `listing` fits the column's width.

Revision ID: b255a1c2d3e4
Revises: b253a1c2d3e4
Create Date: 2026-11-01 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b255a1c2d3e4"
down_revision: str | None = "b253a1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "watch_kind"


def _check(kinds: Sequence[str]) -> None:
    values = ", ".join(f"'{kind}'" for kind in kinds)
    op.create_check_constraint(CONSTRAINT, "watches", f"kind IN ({values})")


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "watches", type_="check")
    _check(("sitemap", "feed", "listing"))


def downgrade() -> None:
    # A listing watch has no meaning before this revision; it goes rather than becoming a
    # sitemap watch that would fail on every read.
    op.execute("DELETE FROM watches WHERE kind = 'listing'")
    op.drop_constraint(CONSTRAINT, "watches", type_="check")
    _check(("sitemap", "feed"))
