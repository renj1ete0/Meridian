"""Unreadable passages kept out of the corpus, at the cut and in what is stored (task `B-235`).

Against a real Postgres because the backfill asks the database for candidates with a regular
expression written once for both Python and Postgres, and the two must read it alike.
"""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select, text

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import Chunk
from meridian_core.sources import upsert_source
from worker.cleancut import clean_cut
from worker.extract.readable import UNREADABLE_CLASS, unreadable_char
from worker.unreadable import run_pass

pytestmark = pytest.mark.usefixtures("require_db")

PARAGRAPH = (
    "Residents were asked how far they walk to reach a bus stop and how the route felt. "
    "Most said the quality of the footpath mattered more than its length. "
) * 7


def garble(text_: str) -> str:
    """As a font with no Unicode map gives it: every code point moved down by 29."""
    return "".join(chr(ord(c) - 29) if ord(c) > 29 else c for c in text_)


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


# Each side of every range in the class, and the layout controls just outside them.
BOUNDARIES = [
    0x01, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x1F, 0x20, 0x7E, 0x7F,
    0x84, 0x85, 0x86, 0x9F, 0xA0, 0xDFFF + 1, 0xE000, 0xF8FF, 0xF900, 0xFFFD, 0xFFFE,
    0xEFFFF, 0xF0000, 0xFFFFD, 0xFFFFE, 0x100000, 0x10FFFD, 0x10FFFF,
]  # fmt: skip


async def test_postgres_reads_the_class_as_python_and_the_test_do(sess) -> None:
    """Drift: a character Postgres's regex leaves out is a passage the backfill never sees."""
    rows = await sess.execute(
        text("SELECT cp, chr(cp) ~ :cls FROM unnest(CAST(:cps AS int[])) AS cp"),
        {"cls": UNREADABLE_CLASS, "cps": BOUNDARIES},
    )
    in_postgres = dict(rows.all())
    python = re.compile(UNREADABLE_CLASS)
    for cp in BOUNDARIES:
        expected = unreadable_char(chr(cp))
        assert bool(python.fullmatch(chr(cp))) == expected, hex(cp)
        assert in_postgres[cp] == expected, hex(cp)


async def a_source(sess, texts: list[str]) -> tuple[int, list[int]]:
    source, _ = await upsert_source(
        sess, f"https://u{uuid.uuid4().hex[:10]}.test/r.pdf", checksum=f"sha256:{uuid.uuid4().hex}"
    )
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=t, chunk_index=i) for i, t in enumerate(texts)],
    )
    ids = list(
        await sess.scalars(
            select(Chunk.chunk_id)
            .where(Chunk.source_id == source.source_id)
            .order_by(Chunk.chunk_index)
        )
    )
    return source.source_id, ids


async def test_the_cut_leaves_out_an_unreadable_passage_and_keeps_the_rest_citable(sess) -> None:
    host = f"u{uuid.uuid4().hex[:10]}.test"
    source, _ = await upsert_source(sess, f"https://{host}/r", checksum="sha256:u")
    document = f"{PARAGRAPH}\n\n{garble(PARAGRAPH)}\n\n{PARAGRAPH.upper()}"

    cut = await clean_cut(sess, source.source_id, host, text=document)

    assert cut.unreadable == 1
    assert [c.index for c in cut.chunks] == list(range(len(cut.chunks)))
    assert len(cut.chunks) == 2
    for chunk in cut.chunks:
        assert document[chunk.offset : chunk.end] == chunk.text, "every passage stays a slice"
        assert "\x03" not in chunk.text


async def test_a_readable_document_loses_nothing_to_the_test(sess) -> None:
    host = f"u{uuid.uuid4().hex[:10]}.test"
    source, _ = await upsert_source(sess, f"https://{host}/r", checksum="sha256:r")
    document = f"\x07 {PARAGRAPH}\n\n\x07{PARAGRAPH}\n\nContents\x083\nMethods\x0812"

    cut = await clean_cut(sess, source.source_id, host, text=document)

    assert cut.unreadable == 0
    assert "".join(c.text for c in cut.chunks).count("Residents") == 14


async def test_a_report_writes_nothing(sess) -> None:
    sid, ids = await a_source(sess, [PARAGRAPH, garble(PARAGRAPH)])

    stats = await run_pass(apply=False, source_ids=[sid], session_factory=factory(sess))

    assert stats.unreadable == 1 and stats.candidates == 1
    assert stats.examples[0][0] == ids[1]
    live = await sess.scalars(
        select(Chunk.chunk_id).where(Chunk.source_id == sid, Chunk.superseded_at.is_(None))
    )
    assert sorted(live) == sorted(ids)


async def test_apply_supersedes_the_unreadable_and_nothing_else(sess) -> None:
    """Superseded, not deleted: out of search and embedding, still there for a citation."""
    # A symbol-font bullet: a candidate the database hands over, and readable.
    bulleted = f"\uf0b7 {PARAGRAPH}"
    sid, ids = await a_source(sess, [PARAGRAPH, garble(PARAGRAPH), bulleted])

    stats = await run_pass(apply=True, source_ids=[sid], session_factory=factory(sess))

    assert stats.unreadable == 1 and stats.candidates == 2
    rows = dict(
        (
            await sess.execute(
                select(Chunk.chunk_id, Chunk.superseded_at.is_not(None)).where(
                    Chunk.source_id == sid
                )
            )
        ).all()
    )
    assert rows == {ids[0]: False, ids[1]: True, ids[2]: False}
