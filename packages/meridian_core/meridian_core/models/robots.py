"""The robots.txt cache, kept across restarts (task P1-29, spec §6.1, §2.3.1.3).

The cache was in-process, so a worker restart re-fetched `/robots.txt` for every
origin it touched. Harmless while the crawl is deep and a handful of domains;
wasteful once it is wide, because a restart then costs one extra request per
domain — paid against the same rate limiter the pages queue behind, so the
first minutes after a restart are spent not crawling.

**The raw file is stored, not the parsed rules.** Re-parsing on load is cheap,
the compiled matchers are not serialisable in any form worth versioning, and a
fix to the parser then applies to everything already cached rather than only to
origins fetched afterwards. The file is also the evidence: "why was this URL
refused" is answerable from the row.

**One row per origin, overwritten in place.** The table is therefore bounded by
the number of distinct origins the crawl has ever touched — the same order as
`fetch_policy`, and not a growth curve anybody needs to sweep.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from meridian_core.db import Base

from .mixins import TimestampMixin, constrained

#: What happened when this origin's robots.txt was last read. Kept rather than
#: inferred from `body IS NULL`, because "the server said 404" and "the server
#: could not be reached" both store no body and mean opposite things: the first
#: permits the whole origin, the second refuses it until it can be read
#: (§2.3.1.3 — a crawler that cannot check must not assume permission).
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

    #: Wall clock, deliberately — and this is the one thing about this table
    #: that is easy to get wrong. The in-process cache expires on
    #: `time.monotonic()`, which is correct there and meaningless here: it
    #: counts from an arbitrary origin, usually boot, so a persisted monotonic
    #: deadline would be compared against a different clock after the restart
    #: it exists to survive.
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_robots_cache_expires_at", "expires_at"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RobotsCacheEntry {self.origin} {self.outcome} until {self.expires_at}>"
