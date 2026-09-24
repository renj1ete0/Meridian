"""Boilerplate across a site, and re-chunking around it (task B-43).

Against Postgres because the rules that matter are counts over a table: how many
of a host's pages carry a line, what share that is, and whether a line counted
from cleaned text would switch the rule off. The pure cleaners are tested in
`tests/unit/test_clean.py`.

Every test uses its own host, so the per-host counts cannot see the dev
corpus — `recompute` is global, and rebuilding the dev corpus's set as a side
effect is harmless because that table is derived.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select

from meridian_core.boilerplate import (
    MIN_PAGES,
    boilerplate_for,
    host_key,
    recompute,
    record_page_lines,
)
from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import Chunk, Entity, PageLine, Source
from meridian_core.sources import upsert_source
from worker.cleancut import clean_cut
from worker.extract.clean import line_hash, normalise_line
from worker.rechunk import rebuild_text, run_pass

pytestmark = pytest.mark.usefixtures("require_db")

BANNER = "Beware of scam calls claiming to be from this organisation today"
BODY = (
    "The study measured how far residents walk to reach a bus stop, and found "
    "that distance mattered less than the quality of the route itself."
)


@pytest.fixture
def host() -> str:
    return f"b{uuid.uuid4().hex[:10]}.test"


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


def page(n: int, banner: bool = True) -> str:
    top = f"{BANNER}\n\n" if banner else ""
    return f"{top}{BODY} Page {n} has its own finding, number {n}.\n\nA second paragraph {n}."


async def a_page(sess, host: str, text: str, *, record: bool = True) -> int:
    source, _ = await upsert_source(
        sess, f"https://{host}/p/{uuid.uuid4().hex[:8]}", checksum=f"sha256:{uuid.uuid4().hex}"
    )
    cut = await clean_cut(sess, source.source_id, host, text=text, record_lines=record)
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=c.text, chunk_index=c.index, page_or_offset=c.offset) for c in cut.chunks],
    )
    await sess.flush()
    return source.source_id


def banner_hash() -> int:
    return line_hash(normalise_line(BANNER))


async def live_text(sess, source_id: int) -> str:
    rows = await sess.scalars(
        select(Chunk.text).where(Chunk.source_id == source_id, Chunk.superseded_at.is_(None))
    )
    return "\n".join(rows)


# --------------------------------------------------------------------------
# The host
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.Example.org/a", "example.org"),
        ("https://example.org:8443/a", "example.org"),
        ("https://law.example.edu/a", "law.example.edu"),
        ("https://hospital.example.edu/a", "hospital.example.edu"),
        ("not a url", None),
        (None, None),
    ],
)
def test_the_host_folds_www_and_ports_but_not_subdomains(url, expected) -> None:
    assert host_key(url) == expected


# --------------------------------------------------------------------------
# Counting
# --------------------------------------------------------------------------


async def test_a_line_on_enough_pages_and_share_becomes_boilerplate(sess, host) -> None:
    for n in range(MIN_PAGES):
        await a_page(sess, host, page(n))
    await recompute(sess)

    assert banner_hash() in await boilerplate_for(sess, host)


async def test_a_line_on_too_few_pages_is_not_boilerplate(sess, host) -> None:
    for n in range(MIN_PAGES - 1):
        await a_page(sess, host, page(n))
    await recompute(sess)

    assert banner_hash() not in await boilerplate_for(sess, host)


async def test_a_line_on_enough_pages_but_too_small_a_share_is_not_boilerplate(sess, host) -> None:
    # Five of twenty pages is 25%, under the 30% share.
    for n in range(20):
        await a_page(sess, host, page(n, banner=n < 5))
    await recompute(sess)

    assert banner_hash() not in await boilerplate_for(sess, host)


async def test_another_hosts_banner_is_not_this_hosts(sess, host) -> None:
    for n in range(MIN_PAGES):
        await a_page(sess, host, page(n))
    await recompute(sess)

    assert await boilerplate_for(sess, f"other-{host}") == frozenset()


async def test_a_line_that_drops_under_the_threshold_stops_being_boilerplate(sess, host) -> None:
    ids = [await a_page(sess, host, page(n)) for n in range(MIN_PAGES)]
    await recompute(sess)
    assert banner_hash() in await boilerplate_for(sess, host)

    # The site changed its template: one page re-fetched without the banner.
    await record_page_lines(sess, ids[0], host, [])
    await recompute(sess)

    assert banner_hash() not in await boilerplate_for(sess, host)


@pytest.mark.parametrize(("pages", "share"), [(0, 0.3), (5, 0.0), (5, 1.5)])
async def test_nonsense_thresholds_are_refused(sess, pages, share) -> None:
    with pytest.raises(ValueError):
        await recompute(sess, min_pages=pages, min_share=share)


# --------------------------------------------------------------------------
# Counted from uncleaned text, always
# --------------------------------------------------------------------------


async def test_page_lines_are_counted_before_cleaning(sess, host) -> None:
    """A banner the page's chunks no longer contain is still in its lines —
    otherwise the rule switches itself off by working."""
    for n in range(MIN_PAGES):
        await a_page(sess, host, page(n))
    await recompute(sess)

    later = await a_page(sess, host, page(99))

    assert BANNER not in await live_text(sess, later)
    stored = set(await sess.scalars(select(PageLine.line_hash).where(PageLine.source_id == later)))
    assert banner_hash() in stored


async def test_the_banner_stays_boilerplate_after_every_page_is_cleaned(sess, host) -> None:
    for n in range(MIN_PAGES):
        await a_page(sess, host, page(n))
    await recompute(sess)
    for n in range(MIN_PAGES):
        await a_page(sess, host, page(100 + n))

    await recompute(sess)

    assert banner_hash() in await boilerplate_for(sess, host)


async def test_cleaned_chunks_are_still_slices_of_the_text(sess, host) -> None:
    for n in range(MIN_PAGES):
        await a_page(sess, host, page(n))
    await recompute(sess)
    text = page(7)
    source, _ = await upsert_source(sess, f"https://{host}/slice", checksum="sha256:x")

    cut = await clean_cut(sess, source.source_id, host, text=text)

    assert cut.lines_removed == 1 and cut.reasons == {"repeated": 1}
    for chunk in cut.chunks:
        assert text[chunk.offset : chunk.offset + len(chunk.text)] == chunk.text
        assert BANNER not in chunk.text


async def test_clean_cut_wants_exactly_one_document(sess, host) -> None:
    with pytest.raises(ValueError):
        await clean_cut(sess, 1, host)
    with pytest.raises(ValueError):
        await clean_cut(sess, 1, host, text="a", pages=[])


# --------------------------------------------------------------------------
# The re-chunk pass
# --------------------------------------------------------------------------


def factory(sess):
    @asynccontextmanager
    async def make():
        sess.commit = sess.flush
        yield sess

    return make


async def stored_before_this_task(sess, host: str, n: int) -> int:
    """A page chunked without cleaning and without recorded lines."""
    return await a_page(sess, host, page(n), record=False)


async def test_a_report_pass_changes_no_chunk(sess, host) -> None:
    ids = [await stored_before_this_task(sess, host, n) for n in range(MIN_PAGES)]

    stats = await run_pass(apply=False, domain=host, session_factory=factory(sess))

    assert stats.recorded == MIN_PAGES
    assert stats.changed == MIN_PAGES
    for sid in ids:
        assert BANNER in await live_text(sess, sid)


async def test_an_applied_pass_supersedes_and_keeps_the_old_chunks(sess, host) -> None:
    ids = [await stored_before_this_task(sess, host, n) for n in range(MIN_PAGES)]
    before = await sess.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.source_id == ids[0])
    )

    await run_pass(apply=True, domain=host, session_factory=factory(sess))

    for sid in ids:
        assert BANNER not in await live_text(sess, sid)
    retired = await sess.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.source_id == ids[0], Chunk.superseded_at.is_not(None))
    )
    assert retired == before


async def test_a_cited_source_is_left_alone(sess, host) -> None:
    ids = [await stored_before_this_task(sess, host, n) for n in range(MIN_PAGES)]
    cited_chunk = await sess.scalar(select(Chunk.chunk_id).where(Chunk.source_id == ids[0]))
    sess.add(
        Entity(
            canonical_name=f"e-{uuid.uuid4().hex[:8]}",
            node_type="finding",
            supporting_chunk_ids=[cited_chunk],
        )
    )
    await sess.flush()

    stats = await run_pass(apply=True, domain=host, session_factory=factory(sess))

    assert stats.cited == 1
    assert BANNER in await live_text(sess, ids[0])
    assert BANNER not in await live_text(sess, ids[1])
    assert BANNER not in await live_text(sess, ids[1])


async def test_a_second_pass_finds_nothing_to_change(sess, host) -> None:
    for n in range(MIN_PAGES):
        await stored_before_this_task(sess, host, n)
    await run_pass(apply=True, domain=host, session_factory=factory(sess))

    again = await run_pass(apply=True, domain=host, session_factory=factory(sess))

    assert again.changed == 0 and again.recorded == 0


async def test_rechunked_offsets_point_into_the_original_text(sess, host) -> None:
    text = page(3)
    for n in range(MIN_PAGES):
        await stored_before_this_task(sess, host, n)
    sid = await a_page(sess, host, text, record=False)

    await run_pass(apply=True, domain=host, session_factory=factory(sess))

    rows = (
        await sess.execute(
            select(Chunk.page_or_offset, Chunk.text).where(
                Chunk.source_id == sid, Chunk.superseded_at.is_(None)
            )
        )
    ).all()
    assert rows
    for offset, chunk in rows:
        assert text[offset : offset + len(chunk)] == chunk


def test_rebuilding_refuses_overlapping_chunks() -> None:
    with pytest.raises(ValueError):
        rebuild_text([(0, "abcdef"), (3, "def")])


def test_rebuilding_fills_gaps_with_newlines_only() -> None:
    assert rebuild_text([(0, "ab"), (4, "cd")]) == "ab\n\ncd"


async def test_a_junk_source_is_not_rechunked(sess, host) -> None:
    # One more page than the threshold, so the others still clear it without the junk one.
    ids = [await stored_before_this_task(sess, host, n) for n in range(MIN_PAGES + 1)]
    junk = await sess.get(Source, ids[0])
    junk.retention_tier = "junk"
    await sess.flush()

    await run_pass(apply=True, domain=host, session_factory=factory(sess))

    assert BANNER in await live_text(sess, ids[0])
    assert BANNER not in await live_text(sess, ids[1])
