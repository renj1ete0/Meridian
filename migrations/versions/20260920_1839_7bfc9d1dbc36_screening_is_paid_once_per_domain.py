"""screening is paid once per domain

Task P4-14, §2.5. `P1-23`'s pre-screen flags pages and blocks nothing — its own
task text says so, and until now the log line was the entire mechanism. These
columns are what a flag can act on.

**The verdict lives on the domain; the state lives on the page.** A site with
four thousand pages must not be judged four thousand times, and a domain cleared
on Monday must not have page 3,001 quarantined on Friday because that page
happened to quote an instruction. So `fetch_policy` carries the verdict and the
counter that earns it, and `sources.trust_state` is the page's own copy of the
state it was stored under — which also means clearing a domain later does not
rewrite what was true when each page arrived.

**`unscreened` is the starting state, and it is not `cleared`.** Every existing
row gets it by server default, which is honest: nothing has looked at them. It
is also why the filter the slow loop uses admits `cleared` explicitly rather
than excluding `quarantined` — "not obviously bad" and "checked" are different
claims, and only one of them should feed a model.

The CHECK constraints are created here by `op.add_column`, which was worth
verifying rather than assuming: `P0-21` found that autogenerate misses CHECKs on
*existing* columns, and a reasonable reading was that it would miss these too.
It does not — both were confirmed present, and confirmed to refuse a bad value,
against the real server.


Revision ID: 7bfc9d1dbc36
Revises: 070a7b2e2deb
Create Date: 2026-09-20 18:39:39.089216
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7bfc9d1dbc36"
down_revision: str | None = "070a7b2e2deb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fetch_policy",
        sa.Column(
            "trust_state",
            sa.Enum(
                "unscreened",
                "cleared",
                "quarantined",
                "rejected",
                name="trust_state",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="unscreened",
            nullable=False,
        ),
    )
    op.add_column(
        "fetch_policy",
        sa.Column("clean_fetches", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "fetch_policy", sa.Column("trust_decided_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("fetch_policy", sa.Column("trust_decided_by", sa.Text(), nullable=True))
    op.add_column("fetch_policy", sa.Column("trust_reason", sa.Text(), nullable=True))
    op.add_column(
        "sources",
        sa.Column(
            "trust_state",
            sa.Enum(
                "unscreened",
                "cleared",
                "quarantined",
                "rejected",
                name="trust_state",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="unscreened",
            nullable=False,
        ),
    )
    op.create_index(op.f("ix_sources_trust_state"), "sources", ["trust_state"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sources_trust_state"), table_name="sources")
    op.drop_column("sources", "trust_state")
    op.drop_column("fetch_policy", "trust_reason")
    op.drop_column("fetch_policy", "trust_decided_by")
    op.drop_column("fetch_policy", "trust_decided_at")
    op.drop_column("fetch_policy", "clean_fetches")
    op.drop_column("fetch_policy", "trust_state")
