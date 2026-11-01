"""One counting definition per quantity, wherever it is shown (task `B-240`).

A reviewer found the same corpus quoted with two source counts a fifth apart and five different
passage figures, because each page wrote its own predicate. These tests write a small corpus
holding every kind of row a count can disagree on (junk, a copied source, a copied passage, a
superseded passage, an unlabelled and an unscreened source) and hold every surface to the
canonical functions in `meridian_core.stats` and to what search itself returns.

Counts are deltas over a rolled-back session, so rows a crawled dev database already holds do
not matter (docs/handover.md, "A dev database that has actually crawled").
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import func, select, update

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.growth import growth
from meridian_core.models import Chunk, ChunkTopics, Source
from meridian_core.search import SearchFilters, search
from meridian_core.sources import upsert_source
from meridian_core.stats import corpus_stats, kept_sources, searchable_passages
from meridian_core.topicoverlaps import topic_overlaps
from worker.digest import corpus_lines

pytestmark = pytest.mark.usefixtures("require_db")

#: Far enough ahead that "everything up to now" is every row, these tests' included.
LATER = dt.datetime(2999, 1, 1, tzinfo=dt.UTC)


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


async def a_source(
    sess,
    token: str,
    passages: int,
    *,
    labels: list[str] | None,
    trust: str = "cleared",
    junk: bool = False,
    copy_of: Source | None = None,
) -> tuple[Source, list[int]]:
    """A source with ``passages`` live passages, each carrying ``token`` and ``labels``."""
    source, _ = await upsert_source(
        sess,
        f"https://counts-{uuid.uuid4().hex[:10]}.example.test/{uuid.uuid4().hex}",
        checksum=f"sha256:{uuid.uuid4().hex}",
    )
    await sess.execute(
        update(Source)
        .where(Source.source_id == source.source_id)
        .values(
            topic_labels=labels,
            trust_state=trust,
            retention_tier="junk" if junk else "background",
            duplicate_of=copy_of.source_id if copy_of else None,
            duplicate_reason="exact" if copy_of else None,
        )
    )
    await replace_chunks(
        sess,
        source.source_id,
        [
            ChunkWrite(text=f"Passage {i} about {token} {uuid.uuid4().hex}.", chunk_index=i)
            for i in range(passages)
        ],
    )
    ids = list(
        await sess.scalars(
            select(Chunk.chunk_id)
            .where(Chunk.source_id == source.source_id)
            .order_by(Chunk.chunk_index)
        )
    )
    if labels:
        for chunk_id in ids:
            sess.add(
                ChunkTopics(
                    chunk_id=chunk_id, topic_labels=labels, topic_scores={}, topic_basis="t"
                )
            )
    await sess.flush()
    return source, ids


class Snapshot:
    """Every surface's figure for each named quantity, at one moment."""

    def __init__(self, stats, grown, overlaps, digest: list[str]) -> None:
        self.stats = stats
        self.grown = grown
        self.overlaps = overlaps
        self.digest = digest

    @classmethod
    async def take(cls, sess, topic: str) -> Snapshot:
        return cls(
            await corpus_stats(sess),
            await growth(sess, zone="UTC", days=None, topics=[topic], now=LATER),
            await topic_overlaps(sess),
            await corpus_lines(sess, dt.datetime.now(dt.UTC)),
        )


@pytest.fixture
async def world(sess):
    """The corpus every test below reads, and a snapshot taken before it was written."""
    token = f"zqcount{uuid.uuid4().hex[:10]}"
    topic = f"counts-{uuid.uuid4().hex[:8]}"
    before = await Snapshot.take(sess, topic)

    kept, (first, _second, copied, retired) = await a_source(sess, token, 4, labels=[topic])
    # A passage marked a copy of another, and one superseded by a re-extraction.
    await sess.execute(
        update(Chunk)
        .where(Chunk.chunk_id == copied)
        .values(duplicate_of=first, nearest_similarity=0.99)
    )
    await sess.execute(
        update(Chunk).where(Chunk.chunk_id == retired).values(superseded_at=func.now())
    )
    _, junk_ids = await a_source(sess, token, 2, labels=[topic], junk=True)
    _, copy_ids = await a_source(sess, token, 2, labels=[topic], copy_of=kept)
    _, unread_ids = await a_source(sess, token, 2, labels=None, trust="unscreened")
    await sess.flush()

    return {
        "sess": sess,
        "token": token,
        "topic": topic,
        "before": before,
        "after": await Snapshot.take(sess, topic),
        # What a reader can find: two of the kept source's four, and the unlabelled source's two.
        "searchable": {first, _second, *unread_ids},
        "not_searchable": {copied, retired, *junk_ids, *copy_ids},
    }


async def test_every_surface_counts_sources_with_one_definition(world) -> None:
    """The landing, Growth, the topic web and the digest agree on how many sources arrived.

    Four rows were fetched; a junk page and a copy of another are not sources (`B-156`).
    """
    before, after = world["before"], world["after"]

    assert after.stats.fetched_sources - before.stats.fetched_sources == 4
    added = after.stats.kept_sources - before.stats.kept_sources
    assert added == 2

    assert after.overlaps.kept_sources == after.stats.kept_sources
    assert after.digest[0].startswith(f"corpus: +{_plus(before.digest, 0) + added} sources kept")
    # Growth, narrowed to this test's topic, sees the one kept source that carries it.
    assert after.grown.sources.total - before.grown.sources.total == 1
    assert after.overlaps.labelled_sources - before.overlaps.labelled_sources == 1


async def test_unnarrowed_growth_counts_the_landings_sources(world) -> None:
    sess = world["sess"]
    grown = await growth(sess, zone="UTC", days=None, now=LATER)
    assert grown.sources.total == (await corpus_stats(sess)).kept_sources


async def test_searchable_passages_are_what_find_can_return(world) -> None:
    """The count and the search are one predicate: the passages a word search finds for a
    word only these rows carry are exactly the rows the count added."""
    sess, before, after = world["sess"], world["before"], world["after"]

    found = await search(sess, world["token"], filters=SearchFilters(), limit=100, candidates=100)
    hits = {hit.chunk_id for hit in found.hits}

    assert hits == world["searchable"]
    assert not hits & world["not_searchable"]
    assert after.stats.searchable_chunks - before.stats.searchable_chunks == len(hits)
    assert after.digest[0].endswith(f"+{_plus(before.digest, 1) + len(hits)} searchable passages")


async def test_readable_passages_are_what_an_assistant_can_search(world) -> None:
    """`readable_chunks` is the cleared part: what the MCP search tool's filters reach."""
    sess, before, after = world["sess"], world["before"], world["after"]

    found = await search(
        sess, world["token"], filters=SearchFilters(cleared_only=True), limit=100, candidates=100
    )

    assert after.stats.readable_chunks - before.stats.readable_chunks == len(found.hits) == 2


async def test_growths_passages_on_a_topic_are_searchable_passages(world) -> None:
    """Growth counted every live labelled passage, copies and junk included: here 7, not 2."""
    before, after = world["before"], world["after"]
    assert after.grown.passages.total - before.grown.passages.total == 2
    topic = world["topic"]
    by_topic = {t.topic: t.passages.total for t in after.grown.all_topics}
    assert by_topic[topic] == 2


async def test_new_since_counts_with_the_same_definitions(world) -> None:
    """The returning reader's delta is in kept sources and searchable passages too."""
    sess = world["sess"]
    since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
    stats = await corpus_stats(sess, since=since)
    sources_now = await sess.scalar(
        select(func.count()).select_from(Source).where(Source.created_at > since, *kept_sources())
    )
    passages_now = await sess.scalar(
        select(func.count())
        .select_from(Chunk)
        .join(Source, Source.source_id == Chunk.source_id)
        .where(Chunk.created_at > since, *searchable_passages())
    )
    assert stats.new_sources == sources_now >= 2
    assert stats.new_chunks == passages_now >= len(world["searchable"])


async def test_kept_sources_is_searchs_own_source_predicate() -> None:
    """Search's defaults narrow sources by exactly `kept_sources()`, written once each; a
    change to either that the other lacks fails here before it reaches a page."""
    compiled = {str(c.compile()) for c in searchable_passages()}
    for condition in kept_sources():
        assert str(condition.compile()) in compiled


def _plus(lines: list[str], which: int) -> int:
    """The n-th ``+N`` on the digest's corpus line."""
    return int(lines[0].split("+")[which + 1].split()[0])
