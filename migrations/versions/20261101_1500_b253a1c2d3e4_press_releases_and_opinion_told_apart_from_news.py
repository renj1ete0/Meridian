"""press releases and opinion told apart from news (task B-253)

Two document kinds join `sources.doc_kind`: `press_release` (an organisation speaking for
itself) and `opinion` (an outlet's commentary). The CHECK is rewritten by hand, as the one
it replaces was (autogenerate does not see CHECKs on existing tables), and the column widens:
it was sized to the longest kind it held (`varchar(7)`). Stored sources keep
their kind until the backfill re-derives them (`python -m worker.dockind --all --apply`).

Revision ID: b253a1c2d3e4
Revises: b252a1c2d3e4
Create Date: 2026-11-01 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b253a1c2d3e4"
down_revision: str | None = "b252a1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "doc_kind"

BEFORE = ("paper", "report", "news", "legal", "profile", "listing", "other")
KINDS = (
    "paper",
    "report",
    "news",
    "press_release",
    "opinion",
    "legal",
    "profile",
    "listing",
    "other",
)


def _check(kinds: Sequence[str]) -> None:
    values = ", ".join(f"'{kind}'" for kind in kinds)
    op.create_check_constraint(CONSTRAINT, "sources", f"doc_kind IN ({values})")


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "sources", type_="check")
    op.alter_column(
        "sources", "doc_kind", type_=sa.String(max(map(len, KINDS))), existing_nullable=True
    )
    _check(KINDS)


def downgrade() -> None:
    # Both new kinds were `news` before they were told apart.
    op.execute(
        "UPDATE sources SET doc_kind = 'news' WHERE doc_kind IN ('press_release', 'opinion')"
    )
    op.drop_constraint(CONSTRAINT, "sources", type_="check")
    op.alter_column(
        "sources", "doc_kind", type_=sa.String(max(map(len, BEFORE))), existing_nullable=True
    )
    _check(BEFORE)
