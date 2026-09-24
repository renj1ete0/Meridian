"""Lines a site repeats on page after page (task `B-43`).

`worker.extract.clean` removes a line whose hash is in the host's boilerplate
set, and computes each page's own hashes. This module is the half in between,
which needs the database: it records each page's hashes, and derives from all
of them which lines a host repeats often enough to be furniture — a banner, a
footer, a cookie notice, the sidebar every article carries.

**Counted from uncleaned text.** See `PageLine`: a count taken after cleaning
switches the rule off by working.

**Both a count and a share.** A line on five pages of a five-page site is its
template; the same line on five pages of a thousand is a phrase a few articles
share — a quotation, a standard disclaimer attached to one series. So a line
must be on at least :data:`MIN_PAGES` pages *and* at least :data:`MIN_SHARE` of
the host's pages. Measured over a real crawl: at these values 42 lines on 15
hosts qualified, and every one read as template when checked by hand; at 10%
share, article-series boilerplate that is part of each article's content
started to qualify.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from urllib.parse import urlsplit

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import BoilerplateLine, PageLine

log = get_logger(__name__)

#: A line on fewer pages than this is never boilerplate, whatever the share.
MIN_PAGES = 5

#: …nor on a smaller share of its host's pages than this.
MIN_SHARE = 0.3


def host_key(url: str | None) -> str | None:
    """The host a page belongs to, as boilerplate counts it.

    Lower-cased, port dropped, and a leading ``www.`` folded, because a site
    served on both is one template. Subdomains are *not* folded into their
    parent: a university's hospital and its law school share a registrable
    domain and nothing else.
    """
    if not url:
        return None
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


async def record_page_lines(
    sess: AsyncSession, source_id: int, host: str, hashes: Sequence[int]
) -> int:
    """Replace one source's line hashes. Returns how many were written."""
    await sess.execute(delete(PageLine).where(PageLine.source_id == source_id))
    distinct = sorted(set(hashes))
    if distinct:
        await sess.execute(
            insert(PageLine),
            [{"source_id": source_id, "line_hash": h, "host": host} for h in distinct],
        )
    return len(distinct)


async def has_page_lines(sess: AsyncSession, source_id: int) -> bool:
    return bool(
        await sess.scalar(select(func.count()).where(PageLine.source_id == source_id).limit(1))
    )


async def boilerplate_for(sess: AsyncSession, host: str | None) -> frozenset[int]:
    """The line hashes this host repeats, as last computed. Empty for no host."""
    if not host:
        return frozenset()
    rows = await sess.scalars(select(BoilerplateLine.line_hash).where(BoilerplateLine.host == host))
    return frozenset(rows)


@dataclasses.dataclass(frozen=True)
class Recomputed:
    hosts: int
    lines: int


async def recompute(
    sess: AsyncSession, *, min_pages: int = MIN_PAGES, min_share: float = MIN_SHARE
) -> Recomputed:
    """Rebuild `boilerplate_lines` from `page_lines`, wholesale. Does not commit.

    Wholesale because the table is derived: a line that drops under the
    threshold — the site changed its template — must stop being removed, and
    a delete-then-insert in one transaction is the simplest thing that makes
    that true. A reader in another transaction sees the old set or the new one.
    """
    if min_pages < 1 or not 0.0 < min_share <= 1.0:
        raise ValueError("min_pages must be at least 1 and min_share in (0, 1]")
    await sess.execute(delete(BoilerplateLine))
    await sess.execute(
        text(
            """
            INSERT INTO boilerplate_lines (host, line_hash, pages, host_pages)
            SELECT c.host, c.line_hash, c.pages, t.host_pages
              FROM (SELECT host, line_hash, count(*) AS pages
                      FROM page_lines GROUP BY host, line_hash) c
              JOIN (SELECT host, count(DISTINCT source_id) AS host_pages
                      FROM page_lines GROUP BY host) t USING (host)
             WHERE c.pages >= :min_pages
               AND c.pages::float8 / t.host_pages >= :min_share
            """
        ),
        {"min_pages": min_pages, "min_share": min_share},
    )
    lines = int(await sess.scalar(select(func.count()).select_from(BoilerplateLine)) or 0)
    hosts = int(await sess.scalar(select(func.count(func.distinct(BoilerplateLine.host)))) or 0)
    log.info("boilerplate recomputed", extra={"hosts": hosts, "lines": lines})
    return Recomputed(hosts=hosts, lines=lines)
