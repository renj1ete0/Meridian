"""Saved views (task P6-09, spec §12.5): a filter set plus focus node, named and re-openable.

See docs/reference/data-model.md#saved-views for why this is a table.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from meridian_core.db import Base

from .mixins import TimestampMixin, pk


class SavedView(Base, TimestampMixin):
    __tablename__ = "saved_views"

    view_id: Mapped[int] = pk()

    #: Unique, because the name is how somebody refers to it. Two views called
    #: "contested walkability" is a list nobody can use.
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    #: The search text, when the view was saved from one. NULL is a filter-only
    #: view, which §12.3's canvas produces and is not the same as an empty
    #: search — one narrows the whole corpus, the other asks nothing.
    query: Mapped[str | None] = mapped_column(Text)

    #: Exactly the shape `SearchFilters` has; JSON so a new filter needs no migration.
    filters: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    #: `ON DELETE SET NULL`: a merged or deleted entity costs the view its focus,
    #: not the view. The filter set is the part somebody decided to keep.
    focus_entity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("entities.entity_id", ondelete="SET NULL")
    )

    note: Mapped[str | None] = mapped_column(Text)

    #: Drives the landing screen's order. NULL is "never opened", which sorts last
    #: rather than being hidden.
    last_opened_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_saved_views_last_opened", "last_opened_at"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SavedView {self.name!r} focus={self.focus_entity_id}>"
