"""Classifying stored sources, and retiring listings' chunks (task B-59).

`worker.dockind` reads each unclassified source back from its live chunks —
the text the fetch path now classifies — and with ``--apply`` records the kind
and supersedes a listing's chunks. It reports by default, never deletes, and
never retires what anything cites. All of that is a database effect, so it is
tested against Postgres.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select, text

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import Chunk, Entity, Source
from meridian_core.sources import upsert_source
from worker.dockind import run_pass

pytestmark = pytest.mark.usefixtures("require_db")

PROSE = (
    "The survey followed each household for a full year and recorded every trip, "
    "its purpose, its length and the mode chosen. "
) * 3


def feed_chunks(n: int = 15) -> list[str]:
    """A feed as the chunker left it: record rows, each followed by a summary."""
    return [
        f"- [\\[{i}\\]](https://x.test/items/{i})[item:{i:04d}](https://x.test/items/{i})\n"
        f"- Title: Findings on question {i}. {PROSE}"
        for i in range(n)
    ]


@pytest.fixture
def host() -> str:
    return f"k{uuid.uuid4().hex[:10]}.test"


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


async def a_source(sess, host: str, texts: list[str], **fields) -> Source:
    source, _ = await upsert_source(
        sess,
        f"https://{host}/{uuid.uuid4().hex[:8]}",
        checksum=f"sha256:{uuid.uuid4().hex}",
        media_type="text/html",
        text_available=True,
        **fields,
    )
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=t, chunk_index=i, page_or_offset=i * 1000) for i, t in enumerate(texts)],
    )
    await sess.flush()
    return source


async def live(sess, source_id: int) -> int:
    return await sess.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.source_id == source_id, Chunk.superseded_at.is_(None))
    )


async def every(sess, source_id: int) -> int:
    return await sess.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.source_id == source_id)
    )


async def test_a_report_writes_nothing(sess, host) -> None:
    listing = await a_source(sess, host, feed_chunks())

    stats = await run_pass(apply=False, domain=host, session_factory=factory(sess))

    await sess.refresh(listing)
    assert stats.kinds["listing"] == 1
    assert stats.listings_with_chunks == 1 and stats.listing_chunks == 15
    assert listing.doc_kind is None, "a report recorded a kind"
    assert await live(sess, listing.source_id) == 15


async def test_apply_records_the_kind_and_retires_a_listings_chunks(sess, host) -> None:
    listing = await a_source(sess, host, feed_chunks())
    article = await a_source(sess, host, [PROSE * 4, PROSE * 4])

    await run_pass(apply=True, domain=host, session_factory=factory(sess))

    await sess.refresh(listing)
    await sess.refresh(article)
    assert listing.doc_kind == "listing"
    assert listing.extra["doc_kind"]["rule"] == "record_rows"
    assert listing.extra["media_type"] == "text/html", "recording the kind lost the rest of extra"
    assert await live(sess, listing.source_id) == 0
    assert await every(sess, listing.source_id) == 15, "chunks were deleted, not superseded"
    assert article.doc_kind == "other"
    assert await live(sess, article.source_id) == 2


async def test_a_cited_listing_gets_its_kind_and_keeps_its_chunks(sess, host) -> None:
    listing = await a_source(sess, host, feed_chunks())
    chunk = await sess.scalar(select(Chunk.chunk_id).where(Chunk.source_id == listing.source_id))
    sess.add(
        Entity(
            canonical_name=f"e-{uuid.uuid4().hex[:8]}",
            node_type="finding",
            supporting_chunk_ids=[chunk],
        )
    )
    await sess.flush()

    stats = await run_pass(apply=True, domain=host, session_factory=factory(sess))

    await sess.refresh(listing)
    assert stats.cited == 1
    assert listing.doc_kind == "listing"
    assert await live(sess, listing.source_id) == 15


async def test_a_classified_source_is_skipped_unless_everything_is_asked_for(sess, host) -> None:
    """NULL is the queue; `--all` is how a change to the rules reaches the rest."""
    listing = await a_source(sess, host, feed_chunks())
    listing.doc_kind = "other"
    await sess.flush()

    skipped = await run_pass(apply=True, domain=host, session_factory=factory(sess))
    assert skipped.examined == 0
    assert await live(sess, listing.source_id) == 15

    redone = await run_pass(apply=True, everything=True, domain=host, session_factory=factory(sess))
    await sess.refresh(listing)
    assert redone.examined == 1 and redone.changed == 1
    assert listing.doc_kind == "listing"
    assert await live(sess, listing.source_id) == 0


async def test_junk_is_left_alone(sess, host) -> None:
    junk = await a_source(sess, host, feed_chunks(), retention_tier="junk")

    stats = await run_pass(apply=True, domain=host, session_factory=factory(sess))

    await sess.refresh(junk)
    assert stats.examined == 0 and junk.doc_kind is None


async def test_the_raw_head_supplies_what_the_chunks_cannot(sess, host, tmp_path) -> None:
    """Scholarly head metadata makes a paper, however its stored text is shaped."""
    raw = tmp_path / "raw"
    (raw / host).mkdir(parents=True)
    (raw / host / "page.html").write_text(
        '<html><head><meta name="citation_title" content="Findings"></head><body></body></html>'
    )
    paper = await a_source(sess, host, feed_chunks(), raw_file_path=f"{host}/page.html")
    missing = await a_source(sess, host, feed_chunks(), raw_file_path=f"{host}/gone.html")

    stats = await run_pass(apply=True, domain=host, raw_root=raw, session_factory=factory(sess))

    await sess.refresh(paper)
    await sess.refresh(missing)
    assert stats.heads_read == 1
    assert (paper.doc_kind, paper.extra["doc_kind"]["rule"]) == ("paper", "citation_meta")
    assert await live(sess, paper.source_id) == 15
    # A raw file that is not there is an absent signal, not an error.
    assert missing.doc_kind == "listing"


async def test_the_database_refuses_a_kind_the_model_does_not_know(sess, host) -> None:
    """Raw SQL, not the ORM: `validate_strings` would refuse it in Python and hide
    whether the CHECK exists at all (the handover's `P0-21` trap)."""
    source = await a_source(sess, host, [])
    with pytest.raises(Exception) as exc:
        await sess.execute(
            text("UPDATE sources SET doc_kind = 'webpage' WHERE source_id = :id"),
            {"id": source.source_id},
        )
    assert "ck_sources_doc_kind" in str(exc.value)


async def test_the_database_accepts_every_kind_the_model_declares(sess, host) -> None:
    from meridian_core.models.source import DOC_KIND

    source = await a_source(sess, host, [])
    for kind in DOC_KIND.enums:
        await sess.execute(
            text("UPDATE sources SET doc_kind = :kind WHERE source_id = :id"),
            {"kind": kind, "id": source.source_id},
        )
    await sess.execute(
        text("UPDATE sources SET doc_kind = NULL WHERE source_id = :id"),
        {"id": source.source_id},
    )
