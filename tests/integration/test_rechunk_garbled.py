"""Stored PDFs with a garbled text layer (task B-47).

The extractor now refuses a garbled text layer on arrival (`test_pdf_garbled`).
This file is about the ones already stored: the re-chunk pass must give them
what a fresh fetch would — OCR for a document that is mostly garbled, blanked
pages for one that is only partly — and must leave a cited one alone.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import Chunk, EnrichmentItem, Entity, Source
from meridian_core.sources import upsert_source
from worker.ocr_queue import OCR_ITEM_TYPE
from worker.rechunk import run_pass

pytestmark = pytest.mark.usefixtures("require_db")

READABLE = "The report measured how far residents walked to reach a stop. " * 12
GARBLED = "".join(chr(0x10 + (i % 12)) for i in range(700))


@pytest.fixture
def host() -> str:
    return f"g{uuid.uuid4().hex[:10]}.test"


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


def factory(sess):
    @asynccontextmanager
    async def make():
        sess.commit = sess.flush
        yield sess

    return make


async def a_pdf(sess, host: str, *pages: str) -> int:
    source, _ = await upsert_source(
        sess,
        f"https://{host}/{uuid.uuid4().hex[:8]}.pdf",
        checksum=f"sha256:{uuid.uuid4().hex}",
        media_type="application/pdf",
        text_available=True,
    )
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=t, chunk_index=i, page_or_offset=i + 1) for i, t in enumerate(pages)],
    )
    await sess.flush()
    return source.source_id


async def live(sess, source_id: int) -> list[tuple[int, str]]:
    return list(
        (
            await sess.execute(
                select(Chunk.page_or_offset, Chunk.text)
                .where(Chunk.source_id == source_id, Chunk.superseded_at.is_(None))
                .order_by(Chunk.chunk_index)
            )
        ).all()
    )


async def ocr_rows(sess, source_id: int) -> int:
    return len(
        list(
            await sess.scalars(
                select(EnrichmentItem).where(
                    EnrichmentItem.target_id == source_id,
                    EnrichmentItem.item_type == OCR_ITEM_TYPE,
                )
            )
        )
    )


async def test_a_mostly_garbled_pdf_is_sent_to_ocr_and_keeps_no_live_text(sess, host) -> None:
    sid = await a_pdf(sess, host, GARBLED, GARBLED, READABLE)

    stats = await run_pass(apply=True, domain=host, session_factory=factory(sess))

    assert stats.garbled_to_ocr == 1
    assert await live(sess, sid) == []
    source = await sess.get(Source, sid)
    await sess.refresh(source)
    assert source.text_available is False and source.ocr_applied is False
    assert await ocr_rows(sess, sid) == 1
    # Superseded, not deleted.
    kept = await sess.scalars(select(Chunk).where(Chunk.source_id == sid))
    assert len(list(kept)) == 3


async def test_a_report_pass_sends_nothing_to_ocr(sess, host) -> None:
    sid = await a_pdf(sess, host, GARBLED, GARBLED, READABLE)

    stats = await run_pass(apply=False, domain=host, session_factory=factory(sess))

    assert stats.garbled_to_ocr == 1
    assert len(await live(sess, sid)) == 3
    assert await ocr_rows(sess, sid) == 0


async def test_a_partly_garbled_pdf_keeps_its_readable_pages_and_their_numbers(sess, host) -> None:
    sid = await a_pdf(sess, host, READABLE, GARBLED, READABLE)

    stats = await run_pass(apply=True, domain=host, session_factory=factory(sess))

    assert stats.garbled_pages_blanked == 1
    pages = [page for page, _ in await live(sess, sid)]
    assert 2 not in pages and {1, 3} <= set(pages)
    assert await ocr_rows(sess, sid) == 0


async def test_a_cited_garbled_pdf_is_left_alone(sess, host) -> None:
    sid = await a_pdf(sess, host, GARBLED, GARBLED, READABLE)
    chunk = await sess.scalar(select(Chunk.chunk_id).where(Chunk.source_id == sid))
    sess.add(
        Entity(
            canonical_name=f"e-{uuid.uuid4().hex[:8]}",
            node_type="finding",
            supporting_chunk_ids=[chunk],
        )
    )
    await sess.flush()

    stats = await run_pass(apply=True, domain=host, session_factory=factory(sess))

    assert stats.cited == 1 and stats.garbled_to_ocr == 0
    assert len(await live(sess, sid)) == 3


async def test_a_readable_pdf_is_not_touched_by_the_garbled_rule(sess, host) -> None:
    sid = await a_pdf(sess, host, READABLE, READABLE)

    stats = await run_pass(apply=True, domain=host, session_factory=factory(sess))

    assert stats.garbled_to_ocr == 0 and stats.garbled_pages_blanked == 0
    assert len(await live(sess, sid)) == 2
