"""Embedding by value rather than by age (task B-66).

Against a real Postgres because the tiers are SQL: the host a page belongs to is
worked out from its URL inside the query, and whether that agrees with
`boilerplate.host_key` — which is what `host_scores` is keyed by — is the whole
question for the host tiers. A double could only agree with itself.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select

from meridian_core.boilerplate import host_key
from meridian_core.chunks import (
    EMBED_TIERS,
    SAMPLE_HEAD,
    SAMPLE_STRIDE,
    ChunkWrite,
    _host_of,
    chunks_without_embeddings,
    embed_tier,
    embedding_backlog,
    in_sample,
    replace_chunks,
)
from meridian_core.hostscores import MIN_EXAMINED, OFFTOPIC_SHARE
from meridian_core.models import Chunk, HostScore, QueueTask, Source
from meridian_core.queueing import FOLLOWED_SOURCES
from meridian_core.sources import upsert_source
from meridian_core.topiclabels import TRIAGE_FLOOR
from worker.embed import Backfill
from worker.embeddings import FakeEmbedder
from worker.vectors import LocalEmbedder

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


def host() -> str:
    return f"t{uuid.uuid4().hex[:12]}.test"


async def page(sess, url: str, *, chunks: int = 1, junk: bool = False) -> list[Chunk]:
    source, _ = await upsert_source(sess, url, checksum=f"sha256:{uuid.uuid4().hex}")
    if junk:
        source.retention_tier = "junk"
    await replace_chunks(
        sess,
        source.source_id,
        [
            ChunkWrite(text=f"Passage {i} about {uuid.uuid4().hex}.", chunk_index=i)
            for i in range(chunks)
        ],
    )
    rows = await sess.execute(
        select(Chunk).where(Chunk.source_id == source.source_id).order_by(Chunk.chunk_id)
    )
    return list(rows.scalars())


async def judge(sess, h: str, *, on_topic_share: float) -> None:
    examined = MIN_EXAMINED * 5
    sess.add(
        HostScore(host=h, examined=examined, on_topic=round(examined * on_topic_share), pending=0)
    )
    await sess.flush()


async def queued(sess, url: str, seed_source: str) -> None:
    sess.add(QueueTask(url_or_query=url, task_type="url", seed_source=seed_source, priority=5))
    await sess.flush()


async def tier_of(sess, chunk: Chunk) -> set[str]:
    """Every tier the chunk falls in — which must be at most one."""
    found = set()
    for tier in EMBED_TIERS:
        hit = await sess.scalar(
            select(Chunk.chunk_id)
            .join(Source, Source.source_id == Chunk.source_id)
            .where(Chunk.chunk_id == chunk.chunk_id, embed_tier(tier))
        )
        if hit is not None:
            found.add(tier)
    return found


def backfill(sess, first: Chunk, **kwargs) -> Backfill:
    @asynccontextmanager
    async def make():
        sess.commit = sess.flush
        yield sess

    return Backfill(
        LocalEmbedder(FakeEmbedder()),
        session_factory=make,
        start_after=first.chunk_id - 1,
        **kwargs,
    )


# --------------------------------------------------------------------------
# Which tier a passage is in
# --------------------------------------------------------------------------


async def test_a_search_result_is_first(sess) -> None:
    url = f"https://{host()}/a"
    await queued(sess, url, "search")
    (chunk,) = await page(sess, url)
    assert await tier_of(sess, chunk) == {"first"}


@pytest.mark.parametrize("seed", FOLLOWED_SOURCES)
async def test_a_followed_link_is_not_directed(sess, seed) -> None:
    """The directed claim's definition, not a second list that could drift from it."""
    url = f"https://{host()}/a"
    await queued(sess, url, seed)
    (chunk,) = await page(sess, url)
    assert await tier_of(sess, chunk) == {"then"}


async def test_a_page_of_an_on_topic_host_is_first(sess) -> None:
    h = host()
    await judge(sess, h, on_topic_share=0.3)
    (chunk,) = await page(sess, f"https://{h}/a")
    assert await tier_of(sess, chunk) == {"first"}


async def test_a_page_of_an_off_topic_host_is_last(sess) -> None:
    h = host()
    await judge(sess, h, on_topic_share=0.0)
    (chunk,) = await page(sess, f"https://{h}/a")
    assert await tier_of(sess, chunk) == {"last"}


async def test_a_host_judged_on_too_few_pages_is_not_judged(sess) -> None:
    """The same floor HostPolicy uses: one privacy notice does not condemn a site."""
    h = host()
    sess.add(HostScore(host=h, examined=MIN_EXAMINED - 1, on_topic=0, pending=0))
    await sess.flush()
    (chunk,) = await page(sess, f"https://{h}/a")
    assert await tier_of(sess, chunk) == {"then"}


async def test_the_off_topic_line_is_the_host_policys(sess) -> None:
    """Exactly at the share is on-topic: `share < OFFTOPIC_SHARE` is the policy's test."""
    h = host()
    sess.add(HostScore(host=h, examined=200, on_topic=round(200 * OFFTOPIC_SHARE), pending=0))
    await sess.flush()
    (chunk,) = await page(sess, f"https://{h}/a")
    assert await tier_of(sess, chunk) == {"first"}


async def test_a_directed_page_on_an_off_topic_host_is_still_first(sess) -> None:
    """Somebody asked for it; the host's average is about the pages nobody asked for."""
    h = host()
    await judge(sess, h, on_topic_share=0.0)
    url = f"https://{h}/cited"
    await queued(sess, url, "citation")
    (chunk,) = await page(sess, url)
    assert await tier_of(sess, chunk) == {"first"}


@pytest.mark.parametrize("judged", [None, 0.0, 0.3])
async def test_junk_is_in_no_tier(sess, judged) -> None:
    h = host()
    if judged is not None:
        await judge(sess, h, on_topic_share=judged)
    url = f"https://{h}/a"
    await queued(sess, url, "search")
    (chunk,) = await page(sess, url, junk=True)
    assert await tier_of(sess, chunk) == set()


async def test_an_unknown_tier_is_refused() -> None:
    with pytest.raises(ValueError, match="no embedding tier"):
        embed_tier("soon")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.Example.test/a/b?c=d",
        "http://example.test:8080/",
        "https://sub.www.example.test/x",
        "https://WWW.example.test",
        "https://example.test:443/path:with:colons",
    ],
)
async def test_the_sql_host_matches_host_key(sess, url) -> None:
    """`host_scores` is keyed by `host_key`; a host the SQL spells differently is never judged."""
    assert await sess.scalar(select(_host_of(url))) == host_key(url)


# --------------------------------------------------------------------------
# The backlog backpressure waits on
# --------------------------------------------------------------------------


async def test_the_valuable_backlog_leaves_out_junk_and_off_topic_hosts(sess) -> None:
    before_all = await embedding_backlog(sess)
    before_valuable = await embedding_backlog(sess, valuable_only=True)

    off = host()
    await judge(sess, off, on_topic_share=0.0)
    await page(sess, f"https://{off}/a", chunks=3)
    await page(sess, f"https://{host()}/junk", chunks=2, junk=True)
    await page(sess, f"https://{host()}/plain", chunks=4)

    assert await embedding_backlog(sess) - before_all == 9
    assert await embedding_backlog(sess, valuable_only=True) - before_valuable == 4


# --------------------------------------------------------------------------
# The order the backfill serves them in
# --------------------------------------------------------------------------


async def test_a_directed_passage_goes_before_an_older_ordinary_one(sess) -> None:
    (older,) = await page(sess, f"https://{host()}/old")
    url = f"https://{host()}/searched"
    await queued(sess, url, "search")
    (newer,) = await page(sess, url)
    assert newer.chunk_id > older.chunk_id

    await backfill(sess, older, batch_size=1, max_batches=1).run_once()

    await sess.refresh(newer)
    await sess.refresh(older)
    assert newer.embedding is not None
    assert older.embedding is None


async def test_the_off_topic_tail_comes_last_and_junk_never(sess) -> None:
    off = host()
    await judge(sess, off, on_topic_share=0.0)
    (tail,) = await page(sess, f"https://{off}/a")
    (junk,) = await page(sess, f"https://{host()}/j", junk=True)
    (plain,) = await page(sess, f"https://{host()}/p")

    await backfill(sess, tail, batch_size=1, max_batches=1).run_once()
    await sess.refresh(plain)
    await sess.refresh(tail)
    assert plain.embedding is not None and tail.embedding is None

    stats = await backfill(sess, tail, batch_size=1).run_once()
    for chunk in (tail, junk):
        await sess.refresh(chunk)
    assert tail.embedding is not None
    assert junk.embedding is None
    assert stats.embedded == 1


async def test_a_new_directed_passage_jumps_a_tail_already_being_served(sess) -> None:
    """Higher tiers are re-checked every batch, not once at the start of the pass."""
    off = host()
    await judge(sess, off, on_topic_share=0.0)
    tail = await page(sess, f"https://{off}/a", chunks=3)
    url = f"https://{host()}/searched"
    order: list[str] = []
    fresh: list[Chunk] = []

    class ArrivesMidPass:
        """Embeds, and on the first batch a search result is fetched meanwhile."""

        def __init__(self) -> None:
            self._inner = LocalEmbedder(FakeEmbedder())

        async def embed(self, texts):
            order.extend(texts)
            if len(order) == 1:
                await queued(sess, url, "search")
                fresh.extend(await page(sess, url))
            return await self._inner.embed(texts)

    fill = backfill(sess, tail[0], batch_size=1, max_batches=2)
    fill._embedder = ArrivesMidPass()
    await fill.run_once()

    assert order == [tail[0].text, fresh[0].text]


async def test_the_tier_query_still_pages_by_id(sess) -> None:
    url = f"https://{host()}/searched"
    await queued(sess, url, "search")
    chunks = await page(sess, url, chunks=3)
    got = await chunks_without_embeddings(sess, limit=10, after_id=chunks[0].chunk_id, tier="first")
    assert [c.chunk_id for c in got] == [c.chunk_id for c in chunks[1:]]


# --------------------------------------------------------------------------
# Newest first, in the first tier only (`B-75`)
# --------------------------------------------------------------------------


async def test_the_first_tier_is_served_newest_first(sess) -> None:
    """What a crawl just fetched is what its labels and host judgments wait on."""
    older_url, newer_url = f"https://{host()}/old", f"https://{host()}/new"
    await queued(sess, older_url, "search")
    await queued(sess, newer_url, "search")
    (older,) = await page(sess, older_url)
    (newer,) = await page(sess, newer_url)

    await backfill(sess, older, batch_size=1, max_batches=1).run_once()
    await sess.refresh(older)
    await sess.refresh(newer)

    assert newer.embedding is not None and older.embedding is None


async def test_the_other_tiers_stay_oldest_first(sess) -> None:
    (older,) = await page(sess, f"https://{host()}/a")
    (newer,) = await page(sess, f"https://{host()}/b")

    await backfill(sess, older, batch_size=1, max_batches=1).run_once()
    await sess.refresh(older)
    await sess.refresh(newer)

    assert older.embedding is not None and newer.embedding is None


async def test_a_failing_first_tier_batch_is_stepped_past_not_retried_forever(sess) -> None:
    from worker.embeddings import EmbeddingError

    bad_url, good_url = f"https://{host()}/bad", f"https://{host()}/good"
    await queued(sess, good_url, "search")
    await queued(sess, bad_url, "search")
    (good,) = await page(sess, good_url)
    (bad,) = await page(sess, bad_url)  # newest: tried first
    seen: list[str] = []

    class FailsOnce:
        async def embed(self, texts):
            seen.extend(texts)
            if len(seen) == 1:
                raise EmbeddingError("model fell over")
            return await LocalEmbedder(FakeEmbedder()).embed(texts)

    fill = backfill(sess, good, batch_size=1, max_batches=4)
    fill._embedder = FailsOnce()
    stats = await fill.run_once()
    await sess.refresh(good)
    await sess.refresh(bad)

    assert stats.failed_batches == 1
    assert good.embedding is not None and bad.embedding is None
    assert seen.count(seen[0]) == 1, "the failed batch came round again in the same pass"


# --------------------------------------------------------------------------
# A long document's sample first, the rest on what the sample earned (`B-89`)
# --------------------------------------------------------------------------

#: Long enough to have a rest well past the head, whatever the constants are.
LONG = SAMPLE_HEAD + 4 * SAMPLE_STRIDE + 3


async def sampled_ids(sess, chunks: list[Chunk]) -> set[int]:
    rows = await sess.scalars(
        select(Chunk.chunk_id).where(Chunk.chunk_id.in_([c.chunk_id for c in chunks]), in_sample())
    )
    return set(rows)


async def label(sess, chunks: list[Chunk], *, sample_best: float | None) -> None:
    source = await sess.get(Source, chunks[0].source_id)
    source.topics_examined_at = func.now()
    source.topic_sample_best = sample_best
    await sess.flush()


async def test_the_sample_spans_the_document_rather_than_its_opening(sess) -> None:
    chunks = await page(sess, f"https://{host()}/long", chunks=LONG)
    sample = await sampled_ids(sess, chunks)
    indices = sorted(c.chunk_index for c in chunks if c.chunk_id in sample)

    assert indices[:SAMPLE_HEAD] == list(range(SAMPLE_HEAD)), "the opening is read"
    assert indices[-1] >= LONG - SAMPLE_STRIDE, "the sample stops short of the end"
    assert len(sample) < len(chunks) / 2, "a long document's sample is most of it"


async def test_a_short_document_is_all_sample(sess) -> None:
    chunks = await page(sess, f"https://{host()}/short", chunks=SAMPLE_HEAD)
    assert await sampled_ids(sess, chunks) == {c.chunk_id for c in chunks}


@pytest.mark.parametrize("seed", ["search", None])
async def test_the_rest_of_an_unread_document_waits_in_the_last_tier(sess, seed) -> None:
    """Directed or not: a search result can be a thousand-page bill too."""
    url = f"https://{host()}/long"
    if seed:
        await queued(sess, url, seed)
    chunks = await page(sess, url, chunks=LONG)
    sample = await sampled_ids(sess, chunks)
    usual = "first" if seed else "then"

    for chunk in chunks:
        assert await tier_of(sess, chunk) == ({usual} if chunk.chunk_id in sample else {"last"})


@pytest.mark.parametrize(
    ("sample_best", "released"),
    [
        (TRIAGE_FLOOR, True),  # at the line is enough, as `<` says
        (TRIAGE_FLOOR + 0.1, True),
        (TRIAGE_FLOOR - 0.001, False),
        (None, True),  # labelled from the whole text, then re-crawled: nothing to hold on
    ],
)
async def test_the_rest_follows_what_the_sample_earned(sess, sample_best, released) -> None:
    chunks = await page(sess, f"https://{host()}/long", chunks=LONG)
    await label(sess, chunks, sample_best=sample_best)
    rest = [c for c in chunks if c.chunk_id not in await sampled_ids(sess, chunks)]

    for chunk in rest:
        # Exactly one tier either way: a NULL comparison that fell out of every
        # tier would leave the passage unembedded for good, silently.
        assert await tier_of(sess, chunk) == ({"then"} if released else {"last"})


async def test_an_off_topic_host_keeps_a_released_rest_last(sess) -> None:
    """Holding adds a reason to wait; it never overrides the host's."""
    h = host()
    await judge(sess, h, on_topic_share=0.0)
    chunks = await page(sess, f"https://{h}/long", chunks=LONG)
    await label(sess, chunks, sample_best=0.9)
    assert {t for c in chunks for t in await tier_of(sess, c)} == {"last"}


async def test_a_held_rest_is_not_what_backpressure_waits_for(sess) -> None:
    before = await embedding_backlog(sess, valuable_only=True)
    chunks = await page(sess, f"https://{host()}/long", chunks=LONG)
    sample = await sampled_ids(sess, chunks)

    assert await embedding_backlog(sess, valuable_only=True) - before == len(sample)
    await label(sess, chunks, sample_best=0.9)
    assert await embedding_backlog(sess, valuable_only=True) - before == LONG


async def test_a_later_documents_sample_goes_before_an_earlier_ones_rest(sess) -> None:
    earlier = await page(sess, f"https://{host()}/a", chunks=LONG)
    (later,) = await page(sess, f"https://{host()}/b")
    sample = await sampled_ids(sess, earlier)

    await backfill(sess, earlier[0], batch_size=LONG, max_batches=1).run_once()
    for chunk in (*earlier, later):
        await sess.refresh(chunk)

    assert later.embedding is not None
    assert {c.chunk_id for c in earlier if c.embedding is not None} == sample


# --------------------------------------------------------------------------
# A copy of an earlier source waits behind everything else (`B-127`)
# --------------------------------------------------------------------------


async def mark_copy(sess, chunks: list[Chunk], *, of: list[Chunk], reason: str = "exact") -> None:
    source = await sess.get(Source, chunks[0].source_id)
    source.duplicate_of = of[0].source_id
    source.duplicate_reason = reason
    await sess.flush()


@pytest.mark.parametrize("reason", ["exact", "near", "translation"])
@pytest.mark.parametrize("seed", ["search", None])
async def test_a_copy_is_last_however_it_was_found(sess, seed, reason) -> None:
    """Directed or not: a search result that is a mirror answers nothing its original does not."""
    canonical = await page(sess, f"https://{host()}/original")
    url = f"https://{host()}/mirror"
    if seed:
        await queued(sess, url, seed)
    copy = await page(sess, url, chunks=3)
    await mark_copy(sess, copy, of=canonical, reason=reason)

    for chunk in copy:
        assert await tier_of(sess, chunk) == {"last"}
    assert await tier_of(sess, canonical[0]) == {"then"}, "the original is not held with it"


async def test_a_copy_on_an_on_topic_host_is_still_last(sess) -> None:
    """The host's standing is about new pages; this one is not new."""
    h = host()
    await judge(sess, h, on_topic_share=0.9)
    canonical = await page(sess, f"https://{host()}/original")
    copy = await page(sess, f"https://{h}/mirror")
    await mark_copy(sess, copy, of=canonical)
    assert await tier_of(sess, copy[0]) == {"last"}


async def test_a_junk_copy_is_in_no_tier(sess) -> None:
    canonical = await page(sess, f"https://{host()}/original")
    copy = await page(sess, f"https://{host()}/mirror", junk=True)
    await mark_copy(sess, copy, of=canonical)
    assert await tier_of(sess, copy[0]) == set()


async def test_a_cleared_mark_returns_the_copy_to_its_tier(sess) -> None:
    """`worker.docdupes --apply` clears marks that no longer hold; nothing stays stranded."""
    canonical = await page(sess, f"https://{host()}/original")
    url = f"https://{host()}/was-a-mirror"
    await queued(sess, url, "search")
    copy = await page(sess, url)
    await mark_copy(sess, copy, of=canonical)
    assert await tier_of(sess, copy[0]) == {"last"}

    source = await sess.get(Source, copy[0].source_id)
    source.duplicate_of = source.duplicate_reason = None
    await sess.flush()
    assert await tier_of(sess, copy[0]) == {"first"}


async def test_a_copy_is_not_what_backpressure_waits_for(sess) -> None:
    canonical = await page(sess, f"https://{host()}/original")
    before = await embedding_backlog(sess, valuable_only=True)
    copy = await page(sess, f"https://{host()}/mirror", chunks=4)
    assert await embedding_backlog(sess, valuable_only=True) - before == 4

    await mark_copy(sess, copy, of=canonical)
    assert await embedding_backlog(sess, valuable_only=True) - before == 0
    assert await embedding_backlog(sess) >= 4, "still counted in the whole backlog"


async def test_an_older_copy_waits_behind_a_newer_ordinary_page(sess) -> None:
    canonical = await page(sess, f"https://{host()}/original")
    (copy,) = await page(sess, f"https://{host()}/mirror")
    await mark_copy(sess, [copy], of=canonical)
    (plain,) = await page(sess, f"https://{host()}/plain")

    await backfill(sess, canonical[0], batch_size=1, max_batches=2).run_once()
    for chunk in (copy, plain, canonical[0]):
        await sess.refresh(chunk)
    assert plain.embedding is not None and canonical[0].embedding is not None
    assert copy.embedding is None


# --------------------------------------------------------------------------
# A very long document's sample has a higher bar (`B-133`, ADR 0006)
# --------------------------------------------------------------------------


@pytest.fixture
def short_long_document(monkeypatch):
    """A "long" document of a size a test can write; the rule reads the constant at call time."""
    from meridian_core import topiclabels

    size = SAMPLE_HEAD + 2 * SAMPLE_STRIDE + 1
    monkeypatch.setattr(topiclabels, "LONG_DOCUMENT", size)
    return size


def test_the_long_floor_sits_between_the_triage_and_label_floors() -> None:
    from meridian_core.topiclabels import LABEL_FLOOR, LONG_TRIAGE_FLOOR, triage_floor

    assert TRIAGE_FLOOR < LONG_TRIAGE_FLOOR < LABEL_FLOOR
    assert triage_floor(long_document=False) == TRIAGE_FLOOR
    assert triage_floor(long_document=True) == LONG_TRIAGE_FLOOR


@pytest.mark.parametrize(
    ("extra", "score_at", "held"),
    [
        (0, "between", True),  # exactly the long size is long
        (-1, "between", False),  # one passage short is not
        (0, "long_floor", False),  # at the long floor is enough, as `<` says
        (0, "below", True),  # under the ordinary floor is held at any length
        (-1, "below", True),
    ],
)
async def test_the_rest_of_a_long_document_needs_the_higher_bar(
    sess, short_long_document, extra, score_at, held
) -> None:
    from meridian_core.topiclabels import LONG_TRIAGE_FLOOR

    score = {
        "between": (TRIAGE_FLOOR + LONG_TRIAGE_FLOOR) / 2,
        "long_floor": LONG_TRIAGE_FLOOR,
        "below": TRIAGE_FLOOR - 0.01,
    }[score_at]
    chunks = await page(sess, f"https://{host()}/doc", chunks=short_long_document + extra)
    await label(sess, chunks, sample_best=score)
    rest = [c for c in chunks if c.chunk_id not in await sampled_ids(sess, chunks)]

    for chunk in rest:
        assert await tier_of(sess, chunk) == ({"last"} if held else {"then"})


async def test_superseded_passages_do_not_make_a_document_long(sess, short_long_document) -> None:
    """Only live text counts: a page that shrank on re-crawl is judged at its new length."""
    from meridian_core.topiclabels import LONG_TRIAGE_FLOOR

    chunks = await page(sess, f"https://{host()}/shrank", chunks=short_long_document)
    for chunk in chunks[-1:]:
        chunk.superseded_at = func.now()
    await sess.flush()
    await label(sess, chunks, sample_best=(TRIAGE_FLOOR + LONG_TRIAGE_FLOOR) / 2)
    rest = [c for c in chunks[:-1] if c.chunk_id not in await sampled_ids(sess, chunks)]
    assert rest
    for chunk in rest:
        assert await tier_of(sess, chunk) == {"then"}


async def test_the_reports_long_documents_are_the_rules_long_documents(
    sess, short_long_document
) -> None:
    """`retopic` counts what is held with `long_sources`; it must agree with the tier rule."""
    from meridian_core.topiclabels import long_sources

    long_one = await page(sess, f"https://{host()}/long", chunks=short_long_document)
    short_one = await page(sess, f"https://{host()}/short", chunks=short_long_document - 1)
    ids = [long_one[0].source_id, short_one[0].source_id]

    assert await long_sources(sess, ids) == {long_one[0].source_id}
