"""Sites the corpus holds sources from, for Find's site filter (task `B-254`).

A site is a host as `chunks.host_of` writes it. Counted as Find would show them: junk and
copies left out. See docs/features/search.md#filters.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .chunks import host_of
from .models import Source
from .schemas.search import SiteRead, SitesRead

DEFAULT_SITES = 50
MAX_SITES = 500


async def site_counts(
    sess: AsyncSession,
    *,
    doc_kinds: Sequence[str] | None = None,
    source_tiers: Sequence[str] | None = None,
    contains: str | None = None,
    limit: int = DEFAULT_SITES,
) -> SitesRead:
    """Sites by source count, most first, then by name; at most `limit`.

    `contains` keeps sites whose host contains it (case-insensitive), so a reader can find an
    outlet past the first page by typing part of its name.
    """
    site = host_of(Source.url).label("site")
    query = (
        select(site, func.count().label("n"), func.max(Source.publication_date))
        .where(Source.retention_tier != "junk", Source.duplicate_of.is_(None))
        .group_by(site)
        .order_by(func.count().desc(), site)
        .limit(max(1, min(limit, MAX_SITES)))
    )
    if doc_kinds:
        query = query.where(Source.doc_kind.in_(list(doc_kinds)))
    if source_tiers:
        query = query.where(Source.source_tier.in_(list(source_tiers)))
    needle = (contains or "").strip().lower()
    if needle:
        query = query.where(host_of(Source.url).contains(needle, autoescape=True))
    rows = (await sess.execute(query)).all()
    return SitesRead(
        sites=[SiteRead(site=name, sources=n, latest=latest) for name, n, latest in rows if name]
    )
