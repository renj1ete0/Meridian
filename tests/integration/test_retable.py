"""Stored PDFs re-extracted for their tables (task `B-215`).

The pass reads a stored PDF's raw file again and replaces its passages only when that
extraction places a table. The rules held here: the table arrives as rows and the old
passages are superseded, not deleted; a report writes nothing; a cited source, a file that is
not the one fetched and a PDF with no table are each left exactly as they were; and a second
run changes nothing.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select, update

from meridian_core.chunks import ChunkWrite, carry_embeddings, replace_chunks
from meridian_core.models import Chunk, Entity
from meridian_core.sources import upsert_source
from worker import rawstore
from worker.extract.pdf import available
from worker.retable import run_pass

pytestmark = [
    pytest.mark.usefixtures("require_db"),
    pytest.mark.skipif(
        not available() or shutil.which("ps2pdf") is None,
        reason="needs poppler (pdftotext) and ghostscript (ps2pdf) to build real PDFs",
    ),
]

BODY = "Ridership on the line rose over the period against a network average of four per cent."
ROWS = [
    ("Measure", "Before", "After"),
    ("Morning trips", "1204", "1388"),
    ("Evening trips", "986", "1101"),
    ("Weekend trips", "412", "530"),
]


def build(tmp_path: Path, rows) -> bytes:
    """A page of prose with a table whose columns reading order takes one by one."""
    lines = ["/Helvetica findfont 11 scalefont setfont"]
    y = 720
    for _ in range(2):
        lines.append(f"72 {y} moveto ({BODY}) show")
        y -= 16
    y -= 24
    for row in rows:
        for x, cell in zip((72, 300, 420), row, strict=False):
            lines.append(f"{x} {y} moveto ({cell}) show")
        y -= 16
    y -= 24
    lines.append(f"72 {y} moveto ({BODY}) show")
    name = uuid.uuid4().hex[:8]
    source = tmp_path / f"{name}.ps"
    source.write_text(
        "%!PS-Adobe-3.0\n%%Pages: 1\n%%Page: 1 1\n" + "\n".join(lines) + "\nshowpage\n%%EOF\n"
    )
    out = tmp_path / f"{name}.pdf"
    subprocess.run(["ps2pdf", str(source), str(out)], check=True, capture_output=True)
    return out.read_bytes()


def reading_order(content: bytes) -> str:
    """What extraction stored before `B-214`: the page in reading order."""
    out = subprocess.run(
        ["pdftotext", "-q", "-enc", "UTF-8", "-", "-"], input=content, capture_output=True
    )
    return out.stdout.decode().split("\f")[0].strip()


@pytest.fixture
def host() -> str:
    return f"t{uuid.uuid4().hex[:10]}.test"


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


async def stored_pdf(sess, host: str, root: Path, content: bytes, *, checksum=None) -> int:
    url = f"https://{host}/{uuid.uuid4().hex[:8]}.pdf"
    raw = rawstore.store(
        url, content, source_tier="government", media_type="application/pdf", root=root
    )
    source, _ = await upsert_source(
        sess,
        url,
        checksum=checksum or raw.checksum,
        media_type="application/pdf",
        raw_file_path=raw.path,
        retention_tier=raw.retention_tier,
        text_available=True,
    )
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=reading_order(content), chunk_index=0, page_or_offset=1)],
    )
    await sess.flush()
    return source.source_id


async def live(sess, source_id: int) -> list[str]:
    return list(
        await sess.scalars(
            select(Chunk.text)
            .where(Chunk.source_id == source_id, Chunk.superseded_at.is_(None))
            .order_by(Chunk.chunk_index)
        )
    )


async def go(sess, host, root, *, apply=True):
    return await run_pass(apply=apply, domain=host, root=str(root), session_factory=factory(sess))


async def test_a_stored_table_comes_back_as_rows(sess, host, tmp_path) -> None:
    sid = await stored_pdf(sess, host, tmp_path, build(tmp_path, ROWS))
    before = await live(sess, sid)
    assert not any("| Morning trips |" in text for text in before)

    stats = await go(sess, host, tmp_path)

    assert (stats.changed, stats.tables) == (1, 1)
    text = "\n".join(await live(sess, sid))
    assert "| Morning trips | 1204 | 1388 |" in text
    assert text.startswith(BODY[:40])
    # Superseded, not deleted: a citation of the old passage still resolves.
    every = list(await sess.scalars(select(Chunk.text).where(Chunk.source_id == sid)))
    assert set(before) <= set(every)


async def test_a_report_writes_nothing(sess, host, tmp_path) -> None:
    sid = await stored_pdf(sess, host, tmp_path, build(tmp_path, ROWS))
    before = await live(sess, sid)

    stats = await go(sess, host, tmp_path, apply=False)

    assert stats.changed == 1
    assert await live(sess, sid) == before


async def test_a_second_run_changes_nothing(sess, host, tmp_path) -> None:
    sid = await stored_pdf(sess, host, tmp_path, build(tmp_path, ROWS))
    await go(sess, host, tmp_path)
    after = await live(sess, sid)

    stats = await go(sess, host, tmp_path)

    assert (stats.changed, stats.unchanged) == (0, 1)
    assert await live(sess, sid) == after


async def test_a_cited_source_is_left_alone(sess, host, tmp_path) -> None:
    sid = await stored_pdf(sess, host, tmp_path, build(tmp_path, ROWS))
    chunk = await sess.scalar(select(Chunk.chunk_id).where(Chunk.source_id == sid))
    sess.add(
        Entity(
            canonical_name=f"e-{uuid.uuid4().hex[:8]}",
            node_type="finding",
            supporting_chunk_ids=[chunk],
        )
    )
    await sess.flush()
    before = await live(sess, sid)

    stats = await go(sess, host, tmp_path)

    assert (stats.changed, stats.cited) == (0, 1)
    assert await live(sess, sid) == before


async def test_a_file_that_is_not_the_one_fetched_is_left_alone(sess, host, tmp_path) -> None:
    sid = await stored_pdf(
        sess, host, tmp_path, build(tmp_path, ROWS), checksum="sha256:" + "0" * 64
    )
    before = await live(sess, sid)

    stats = await go(sess, host, tmp_path)

    assert stats.skipped == {"file differs from the fetch": 1}
    assert await live(sess, sid) == before


async def test_a_missing_file_is_counted_and_left_alone(sess, host, tmp_path) -> None:
    sid = await stored_pdf(sess, host, tmp_path, build(tmp_path, ROWS))
    before = await live(sess, sid)

    stats = await go(sess, host, tmp_path / "elsewhere")

    assert stats.skipped == {"file missing": 1}
    assert await live(sess, sid) == before


async def test_a_pdf_with_no_table_is_left_as_it_is(sess, host, tmp_path) -> None:
    # Its stored passage differs from what extraction gives now; with no table to place,
    # that difference is not this pass's to make.
    content = build(tmp_path, [])
    sid = await stored_pdf(sess, host, tmp_path, content)
    await replace_chunks(sess, sid, [ChunkWrite(text=BODY, chunk_index=0, page_or_offset=1)])
    await sess.flush()

    stats = await go(sess, host, tmp_path)

    assert (stats.changed, stats.no_table) == (0, 1)
    assert await live(sess, sid) == [BODY]


async def test_a_passage_whose_text_is_unchanged_keeps_its_vector(sess, host, tmp_path) -> None:
    sid = await stored_pdf(sess, host, tmp_path, build(tmp_path, ROWS))
    table = "| Measure | Before |\n| --- | --- |\n| Trips | 1204 |"
    rows = "| Trips | 986 |\n| Walks | 412 |"
    await replace_chunks(
        sess,
        sid,
        [
            ChunkWrite(text=t, chunk_index=i, page_or_offset=1)
            for i, t in enumerate([BODY, "An old paragraph.", table, rows])
        ],
    )
    vector = [0.5] * 1024
    await sess.execute(
        update(Chunk)
        .where(Chunk.source_id == sid, Chunk.superseded_at.is_(None))
        .values(embedding=vector, embedding_view=1)
    )
    await replace_chunks(
        sess,
        sid,
        [
            ChunkWrite(text=t, chunk_index=i, page_or_offset=1)
            for i, t in enumerate([BODY, "A new paragraph.", table, rows])
        ],
    )

    assert await carry_embeddings(sess, sid) == 1

    embedded = dict(
        (
            await sess.execute(
                select(Chunk.text, Chunk.embedding.is_not(None)).where(
                    Chunk.source_id == sid, Chunk.superseded_at.is_(None)
                )
            )
        ).all()
    )
    # Same text keeps its vector; changed text and table rows, whose vector can carry
    # the header row of the passage before, embed again.
    assert embedded == {BODY: True, "A new paragraph.": False, table: False, rows: False}
