"""The stored-title clean-up (task B-69), against Postgres.

What matters is what it leaves behind: the declared title kept, a guessed one
marked as guessed, a real one untouched, and nothing written without --apply.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import Source
from meridian_core.sources import upsert_source
from worker.retitle import run_pass

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


def scoped(sess):
    """The test's transaction, commits downgraded."""

    @asynccontextmanager
    async def make():
        sess.commit = sess.flush
        yield sess

    return make


async def page(sess, title: str | None, texts: list[str], **fields) -> Source:
    source, _ = await upsert_source(
        sess,
        f"https://t{uuid.uuid4().hex[:10]}.test/p",
        checksum=f"sha256:{uuid.uuid4().hex}",
        title=title,
        **fields,
    )
    await replace_chunks(
        sess, source.source_id, [ChunkWrite(text=t, chunk_index=i) for i, t in enumerate(texts)]
    )
    return source


async def test_a_placeholder_is_replaced_and_the_declared_title_kept(sess) -> None:
    source = await page(
        sess, "untitled", ["Shared autonomous vehicles in microtransit systems\nBody text."]
    )
    await run_pass(apply=True, session_factory=scoped(sess), start_after=source.source_id - 1)
    await sess.refresh(source)

    assert source.title == "Shared autonomous vehicles in microtransit systems"
    assert source.extra["declared_title"] == "untitled"
    assert source.extra["title_from"] == "text"


async def test_a_site_name_is_cleared_when_the_text_offers_nothing(sess) -> None:
    source = await page(sess, "Home", ["We use cookies. Accept all."])
    await run_pass(apply=True, session_factory=scoped(sess), start_after=source.source_id - 1)
    await sess.refresh(source)

    assert source.title is None
    assert source.extra["declared_title"] == "Home"


async def test_a_real_title_is_untouched(sess) -> None:
    source = await page(sess, "A User-driven Design Framework for Robotaxis", ["Body."])
    await run_pass(apply=True, session_factory=scoped(sess), start_after=source.source_id - 1)
    await sess.refresh(source)

    assert source.title == "A User-driven Design Framework for Robotaxis"
    assert "declared_title" not in (source.extra or {})


async def test_a_report_writes_nothing(sess) -> None:
    source = await page(sess, "nan", ["Shared autonomous vehicles in microtransit systems"])
    stats = await run_pass(
        apply=False, session_factory=scoped(sess), start_after=source.source_id - 1
    )
    await sess.refresh(source)

    assert source.title == "nan"
    assert stats.from_text + stats.cleaned + stats.cleared >= 1
    assert (
        await sess.scalar(select(Source.extra).where(Source.source_id == source.source_id))
    ) in (None, {})
