"""Writing the source record (task P1-11, spec §5.2, §5.4, §6.4).

One row per URL, carrying the validators for a conditional request, the checksum of the
bytes as fetched, and the raw file's path (null when the tier keeps none). The upsert
never blanks a column it has nothing new for. See docs/features/extraction.md#the-source-row.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
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
    raw_root: str | None = None,
    source_tier: str | None = None,
    trust_state: str | None = None,
    retention_tier: str | None = None,
    media_type: str | None = None,
    final_url: str | None = None,
    accessed_at: dt.datetime | None = None,
    title: str | None = None,
    author: str | None = None,
    publisher: str | None = None,
    publication_date: dt.date | None = None,
    language: str | None = None,
    doi: str | None = None,
    extractor: str | None = None,
    text_available: bool | None = None,
    crawled_for: Sequence[str] | None = None,
    doc_kind: str | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[Source, bool]:
    """Create or update the source row for ``url``. Flushes; does not commit.

    Returns the row and whether the checksum changed; on False a caller can skip
    re-extraction. Every keyword is optional, and ``None`` means "nothing new", never
    "clear it".
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
    if raw_root is not None:
        # Overwrites: it describes where the file that is there *now* was put,
        # so a re-fetch into a different store must not leave the old answer.
        row.raw_root = raw_root
    if source_tier is not None:
        row.source_tier = _not_lower(row.source_tier if not created else None, source_tier)
    if trust_state is not None:
        # Overwrites, unlike the metadata below (`P4-14`): a re-fetch is a fresh
        # screening.
        row.trust_state = trust_state
    if retention_tier is not None:
        row.retention_tier = retention_tier

    # Bibliographic metadata (§5.2). None is "the page did not say", so a re-fetch
    # never erases what an earlier fetch found.
    if title is not None:
        row.title = title
    if author is not None:
        row.author = author
    if publisher is not None:
        row.publisher = publisher
    if publication_date is not None:
        row.publication_date = publication_date
    if language is not None:
        row.language = language
    if doi is not None:
        row.doi = doi
    if extractor is not None:
        # Overwrites: it describes *this* extraction, not the document.
        row.extractor = extractor
    if crawled_for is not None:
        # Accumulates; never replaces (`P2-14`, `P2-21`). Provenance only: what the
        # source is *about* is `topic_labels`.
        merged = dict.fromkeys([*(row.crawled_for or ()), *crawled_for])
        row.crawled_for = sorted(merged)
    if text_available is not None:
        # Overwrites in both directions: a page that stopped extracting has changed.
        row.text_available = text_available
    if doc_kind is not None:
        # Overwrites (`B-59`): the kind is read off the page as it is now, and
        # a site that turned an article into an index has changed what it is.
        row.doc_kind = doc_kind

    row.accessed_at = accessed_at or _now()

    # Media type and the URL actually served live in `extra`, not columns.
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

    The 304 path: only ``accessed_at`` changes.
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

    So a hand correction in Admin is not reverted by the next crawl.
    """
    if current is None:
        return proposed
    if SOURCE_TIER_RANK.get(proposed, -1) > SOURCE_TIER_RANK.get(current, -1):
        return proposed
    return current
