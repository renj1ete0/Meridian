"""The robots.txt cache, kept across restarts (task P1-29, spec §6.1, §2.3.1.3).

The raw file is stored, not the parsed rules, one row per origin, overwritten in place.
See docs/reference/data-model.md#robots-cache.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from meridian_core.db import Base

from .mixins import TimestampMixin, constrained

#: What happened when this origin's robots.txt was last read. Not inferable from
#: `body IS NULL`: a 404 permits the origin, an unreachable server refuses it (§2.3.1.3).
ROBOTS_OUTCOME = constrained("ok", "missing", "unreachable", name="robots_outcome")


class RobotsCacheEntry(Base, TimestampMixin):
    __tablename__ = "robots_cache"

    #: The `/robots.txt` URL, which is the origin in the only form that matters:
    #: scheme included, because http and https are different origins and a site
    #: may serve different files at each.
    origin: Mapped[str] = mapped_column(Text, primary_key=True)

    outcome: Mapped[str] = mapped_column(ROBOTS_OUTCOME, nullable=False)

    #: The file as served, or NULL when there was none to store. Truncated by
    #: the fetcher's own byte limit long before it reaches here.
    body: Mapped[str | None] = mapped_column(Text)

    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: Wall clock, deliberately: a persisted monotonic deadline would be read against a
    #: different clock after a restart.
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_robots_cache_expires_at", "expires_at"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RobotsCacheEntry {self.origin} {self.outcome} until {self.expires_at}>"
