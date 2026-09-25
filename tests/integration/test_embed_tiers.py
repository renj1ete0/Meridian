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
from sqlalchemy import select

from meridian_core.boilerplate import host_key
from meridian_core.chunks import (
    EMBED_TIERS,
    ChunkWrite,
    _host_of,
    chunks_without_embeddings,
    embed_tier,
    embedding_backlog,
    replace_chunks,
)
from meridian_core.hostscores import MIN_EXAMINED, OFFTOPIC_SHARE
from meridian_core.models import Chunk, HostScore, QueueTask, Source
from meridian_core.queueing import FOLLOWED_SOURCES
from meridian_core.sources import upsert_source
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
