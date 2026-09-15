"""saved views

Task P6-09. §12.5: "a filter set plus focus node, named and re-openable".

A table rather than `localStorage`, and the difference matters more than it
looks. A saved view is a piece of research method — the slice somebody decided
was worth returning to — and putting it in a browser means it is lost by a
cleared cache, invisible from a second device, and absent from the database
snapshot that is supposed to be the whole system. `P6-11`'s last-visit stamp is
in `localStorage` for the opposite reason: it is per-reader, per-device, and
worthless to anybody else.

`filters` is JSONB rather than columns because it is exactly the shape
`SearchFilters` already has, and §12.3's canvas filters will add to it. Columns
would mean a migration per filter, and a view saved before one was added would
be indistinguishable from a view that deliberately left it out.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d4a92c78b6f1"
down_revision: str | None = "c8f1d63a75e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saved_views",
        sa.Column("view_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("filters", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        # SET NULL rather than CASCADE: a merged or deleted entity should cost
        # the view its focus, not the view. The filter set is the part somebody
        # decided was worth keeping.
        sa.Column(
            "focus_entity_id",
            sa.BigInteger(),
            sa.ForeignKey("entities.entity_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("last_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_saved_views_created_at", "saved_views", ["created_at"])
    # What the landing screen reads: most recently opened first, and a view
    # never opened sorts last rather than being absent.
    op.create_index("ix_saved_views_last_opened", "saved_views", ["last_opened_at"])


def downgrade() -> None:
    op.drop_index("ix_saved_views_last_opened", table_name="saved_views")
    op.drop_index("ix_saved_views_created_at", table_name="saved_views")
    op.drop_table("saved_views")
