"""a domain earns its allowance

Task P4-12, §11.4. Whether a domain may be *seeded* is a third question, next
to `status` (may we fetch what is queued) and `trust_state` (may a model read
what came back). Conflating any two of them makes one of the three unanswerable.

**NULL means undecided, and undecided is not permission.** A domain the crawl
reached by following a link earns its allowance by returning novel documents; a
domain a model proposes and nobody has seen stays NULL and waits — the same
shape as a harvested gazetteer term queueing for approval rather than applying
itself (§5.6). A boolean defaulting to false would have been the same thing
said worse: "refused" and "not yet considered" need different screens.

`first_seen_via` is never overwritten. A domain discovered by following a link
and later proposed by a model was still discovered by following a link, and
letting the later event win would erase the provenance this column exists for.

`novel_fetches` counts documents that were actually new, not fetches. A site
serving one page under a thousand URLs would otherwise approve itself on volume.

Revision ID: 762be04bfbce
Revises: 7bfc9d1dbc36
Create Date: 2026-09-20 18:56:55.689064
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "762be04bfbce"
down_revision: str | None = "7bfc9d1dbc36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fetch_policy", sa.Column("seed_allowed", sa.Boolean(), nullable=True))
    op.add_column(
        "fetch_policy",
        sa.Column(
            "first_seen_via",
            sa.Enum(
                "frontier",
                "sitemap",
                "search",
                "citation",
                "doi",
                "model",
                "user",
                "diversity",
                name="seed_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "fetch_policy",
        sa.Column("novel_fetches", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("fetch_policy", "novel_fetches")
    op.drop_column("fetch_policy", "first_seen_via")
    op.drop_column("fetch_policy", "seed_allowed")
