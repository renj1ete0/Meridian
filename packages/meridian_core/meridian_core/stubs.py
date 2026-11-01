"""Sources kept as a stub: only what a paywalled page shows everyone (task `B-256`).

The mark is `sources.extra['stub']`; the worker writes and clears it, and everything that
shows a source reads it from here. See docs/features/extraction.md#paywalled-articles.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .chunks import replace_chunks
from .models import Source

#: The key in `sources.extra` that marks a stub.
STUB_KEY = "stub"


def stub_mark(source: Source) -> dict[str, Any] | None:
    """The source's stub mark, or None when it holds the whole document."""
    mark = (source.extra or {}).get(STUB_KEY)
    return mark if isinstance(mark, dict) else None


def is_stub(source: Source) -> bool:
    """Whether the source holds only a paywalled page's visible part."""
    return stub_mark(source) is not None


def set_stub(source: Source, mark: dict[str, Any] | None) -> None:
    """Mark the source a stub, or clear the mark when ``mark`` is None.

    Assigns a new dict: SQLAlchemy does not track in-place changes to a JSONB value.
    """
    extra = {key: value for key, value in (source.extra or {}).items() if key != STUB_KEY}
    if mark is not None:
        extra[STUB_KEY] = mark
    source.extra = extra or None


def not_stub() -> ColumnElement[bool]:
    """SQL: the source is not a stub. A NULL `extra` is not one."""
    return or_(Source.extra.is_(None), ~Source.extra.has_key(STUB_KEY))


async def replace_stubs(sess: AsyncSession, urls: Iterable[str], *, by_source_id: int) -> list[int]:
    """Retire the stubs stored under ``urls`` in favour of a source holding the article.

    For a document handed in with the address of a page the crawl kept as a stub: the
    stub's passage is superseded (never deleted), its mark cleared, and the source that
    replaced it recorded. Returns the stubs' ids. Flushes; does not commit.
    """
    wanted = sorted({url for url in urls if url})
    if not wanted:
        return []
    rows = (
        await sess.scalars(
            select(Source).where(
                Source.url.in_(wanted),
                Source.source_id != by_source_id,
                Source.extra.has_key(STUB_KEY),
            )
        )
    ).all()
    for source in rows:
        await replace_chunks(sess, source.source_id, [])
        set_stub(source, None)
        source.extra = {**(source.extra or {}), "stub_replaced_by": by_source_id}
        source.text_available = False
    await sess.flush()
    return [source.source_id for source in rows]
