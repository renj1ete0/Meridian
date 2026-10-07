"""Reading the language of stored sources that never declared one (task B-153), against Postgres.

What matters is what it leaves: a detected language marked as read from the text, a page in
another language sent back for topic labels with its passages, an English page's labels left
alone, an unsure page left unknown, and nothing written without --apply.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import Chunk, ChunkTopics, Source
from meridian_core.sources import upsert_source
from worker.relanguage import STALE_BASIS, run_pass

pytestmark = pytest.mark.usefixtures("require_db")

ENGLISH = (
    "Public transport ridership rose sharply after the new bus lanes opened in the city "
    "centre, and the operator reported fewer delays on the routes that gained a lane. "
) * 3
FRENCH = (
    "La fréquentation des transports publics a fortement augmenté après l'ouverture des "
    "nouvelles voies de bus dans le centre-ville, et les retards ont diminué. "
) * 3
BASIS = "basis-under-test"


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


def scoped(sess):
    @asynccontextmanager
    async def make():
        sess.commit = sess.flush
        yield sess

    return make


async def page(sess, text: str) -> Source:
    source, _ = await upsert_source(
        sess, f"https://l{uuid.uuid4().hex[:10]}.test/p", checksum=f"sha256:{uuid.uuid4().hex}"
    )
    await replace_chunks(sess, source.source_id, [ChunkWrite(text=text, chunk_index=0)])
    source.topic_basis = BASIS
    chunk_id = await sess.scalar(select(Chunk.chunk_id).where(Chunk.source_id == source.source_id))
    sess.add(ChunkTopics(chunk_id=chunk_id, topic_labels=[], topic_scores={}, topic_basis=BASIS))
    await sess.flush()
    return source


async def passage_basis(sess, source: Source) -> str:
    return await sess.scalar(
        select(ChunkTopics.topic_basis)
        .join(Chunk, Chunk.chunk_id == ChunkTopics.chunk_id)
        .where(Chunk.source_id == source.source_id)
    )


async def test_each_page_gets_what_its_text_says(sess) -> None:
    french, english, short = (
        await page(sess, FRENCH),
        await page(sess, ENGLISH),
        await page(sess, "Annex B"),
    )

    await run_pass(apply=True, session_factory=scoped(sess), start_after=french.source_id - 1)
    for s in (french, english, short):
        await sess.refresh(s)

    assert (french.language, french.extra.get("language_from")) == ("fr", "text")
    assert french.topic_basis is None, "a page in another language is scored again"
    assert await passage_basis(sess, french) == STALE_BASIS, "and so are its passages"

    assert english.language == "en"
    assert english.topic_basis == BASIS, "unknown was already scored as English"
    assert await passage_basis(sess, english) == BASIS

    assert short.language is None and "language_from" not in (short.extra or {})


async def test_a_report_writes_nothing(sess) -> None:
    french = await page(sess, FRENCH)

    stats = await run_pass(
        apply=False, session_factory=scoped(sess), start_after=french.source_id - 1
    )
    await sess.refresh(french)

    assert stats.relabel >= 1 and stats.by_language["fr"] >= 1
    assert french.language is None and french.topic_basis == BASIS
