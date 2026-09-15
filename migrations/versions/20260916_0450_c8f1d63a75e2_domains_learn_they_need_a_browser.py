"""domains learn they need a browser

Task P1-27. `render_js: auto` fetches statically first and re-fetches through
the browser when the HTML turns out to be a shell. That ordering is right for a
corpus of mostly-static government and academic pages: one cheap request on the
JS-dependent minority, no browser launch on everything else.

But it has no memory. A JS-only domain pays both requests on every page, for
every page, forever — and those requests queue in the same per-domain rate-limit
slot, so the cost is not bandwidth, it is crawl throughput on exactly the
domains that are already slowest.

Two columns, and they are learned state rather than configuration:

  * `render_js_escalations` counts *consecutive* escalations, resetting the
    moment a static fetch turns out to have been enough. The same shape as
    `consecutive_failures` on this table, for the same reason: a domain that
    changes behaviour should stop being treated as though it had not.
  * `render_js_learned_at` stamps when the threshold was crossed, and the
    learning expires. Without that the first version of this is a trap: once a
    domain is going straight to the browser it never fetches statically again,
    so the counter cannot reset and a redesign can never be noticed.

An explicit `render_js` in `settings` always wins. Learning fills the gap where
nobody has decided; it does not overrule somebody who has.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8f1d63a75e2"
down_revision: str | None = "b7e4c1a92f38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fetch_policy",
        sa.Column(
            "render_js_escalations",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "fetch_policy",
        sa.Column("render_js_learned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fetch_policy", "render_js_learned_at")
    op.drop_column("fetch_policy", "render_js_escalations")
