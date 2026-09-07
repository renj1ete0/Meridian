"""Writing the source record (task P1-11, spec §5.2, §5.4, §6.4).

One row per URL, created the first time it is fetched and updated on every
fetch after that. It carries three things nothing else can:

**The validators for next time.** ``etag`` and ``last_modified`` are the whole
mechanism behind §6.4's conditional requests. Until this module existed, the
crawler asked for them, stored them nowhere, and re-downloaded every page in
full on every visit — the 304 path was correct, tested, and unreachable.

**The checksum.** ``sha256:<hex>`` of the bytes as fetched, written whether or
not the raw file was kept. A 200 that returns identical bytes is a page that has
not changed, which is a different and cheaper fact than a 304, and the only
thing that can tell you so is the previous hash.

**Where the bytes went, if they went anywhere.** ``raw_file_path`` is relative
to the raw store's root, and null for a source whose retention tier keeps no
file (§5.4). Null therefore means "deliberately not kept", not "missing" — the
difference matters when something later asks why a citation has no local copy.

The upsert never blanks a column it has nothing new for. A fetch that came back
without an ETag must not erase the one from last week, because the next request
would then stop being conditional and nobody would notice except the bandwidth
graph.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import Source

log = get_logger(__name__)

#: Ordinal for `sources.source_tier`, used only to keep an automatic write from
#: demoting a tier an operator raised by hand — the same rule §11.12 states for
#: quality tier, applied to the field that decides what gets kept.
SOURCE_TIER_RANK = {
    "informal": 0,
    "press": 1,
    "institutional": 2,
    "government": 3,
    "peer_reviewed": 4,
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def get_source(sess: AsyncSession, url: str) -> Source | None:
    """The existing row for ``url``, or None."""
    return await sess.scalar(select(Source).where(Source.url == url))


async def upsert_source(
    sess: AsyncSession,
    url: str,
    *,
    checksum: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    raw_file_path: str | None = None,
    source_tier: str | None = None,
    retention_tier: str | None = None,
    media_type: str | None = None,
    final_url: str | None = None,
    accessed_at: dt.datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[Source, bool]:
    """Create or update the source row for ``url``. Flushes; does not commit.

    Returns the row and whether the content changed — that is, whether the
    checksum differs from the one already stored. A caller that gets False can
    skip re-extraction and re-embedding entirely, which is the cheap half of
    keeping a corpus fresh and the reason the checksum is written even for
    sources whose bytes are not retained.

    Every keyword is optional and ``None`` means "nothing new", never "clear
    it". Only the caller that actually learned something writes it.
    """
    row = await get_source(sess, url)
    created = row is None
    if row is None:
        row = Source(url=url)
        sess.add(row)

    changed = checksum is not None and row.checksum != checksum
    if checksum is not None:
        row.checksum = checksum
    if etag is not None:
        row.etag = etag
    if last_modified is not None:
        row.last_modified = last_modified
    if raw_file_path is not None:
        row.raw_file_path = raw_file_path
    if source_tier is not None:
        row.source_tier = _not_lower(row.source_tier if not created else None, source_tier)
    if retention_tier is not None:
        row.retention_tier = retention_tier
    row.accessed_at = accessed_at or _now()

    # Media type and the URL actually served are not columns — `sources` predates
    # both and neither is worth a migration on its own — but they are exactly
    # what extraction needs to pick a parser, and what a redirect makes
    # ambiguous. `extra` is where the schema already puts this.
    additions: dict[str, Any] = dict(extra or {})
    if media_type is not None:
        additions["media_type"] = media_type
    if final_url is not None and final_url != url:
        additions["final_url"] = final_url
    if additions:
        # Replaced wholesale rather than mutated: SQLAlchemy does not track
        # in-place changes to a JSONB dict, so `row.extra["k"] = v` is a write
        # that silently never reaches the database.
        row.extra = {**(row.extra or {}), **additions}

    await sess.flush()
    return row, changed


async def touch_source(
    sess: AsyncSession, url: str, *, accessed_at: dt.datetime | None = None
) -> Source | None:
    """Record that ``url`` was checked and found unchanged. Returns the row.

    The 304 path. Nothing about the content is new, so nothing about the content
    is written — but a source last verified this morning and one last verified
    in March are different things to a corpus that has to be trusted, and only
    ``accessed_at`` says which this is.
    """
    row = await get_source(sess, url)
    if row is None:
        # A 304 for a URL with no source row means the validators came from
        # somewhere this process did not write. Worth saying so rather than
        # inventing a row with no content behind it.
        log.warning("not_modified for a URL with no source row", extra={"url": url})
        return None
    row.accessed_at = accessed_at or _now()
    await sess.flush()
    return row


def _not_lower(current: str | None, proposed: str) -> str:
    """Keep the higher of two source tiers.

    Tiering is mechanical and deterministic, so the two normally agree. They
    stop agreeing the moment someone corrects a domain by hand in Admin — and a
    correction that the next crawl silently reverts is worse than no correction
    at all.
    """
    if current is None:
        return proposed
    if SOURCE_TIER_RANK.get(proposed, -1) > SOURCE_TIER_RANK.get(current, -1):
        return proposed
    return current
