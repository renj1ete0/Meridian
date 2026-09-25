"""Topics per passage against Postgres (task P2-24).

The pure half — the fingerprint, the listing rule, the thresholds — is in
`tests/unit/test_passagetopics.py`. This file is what only the database can
answer: which chunks the passage queue picks up, what a pass writes, when a
row goes stale, and the consumer the task exists for — a topic filter that
finds a passage about the topic inside a document labelled with something else,
and still refuses passages about nothing it was asked for.

**The vectors are constructed, not embedded**, exactly as in
`test_topic_labeller`: every topic owns an axis, the reference phrases cancel
on a spare axis, so a score is precisely the cosine the test set up.
"""

from __future__ import annotations

import math
import uuid
from contextlib import asynccontextmanager

import numpy as np
import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import Chunk, ChunkTopics, Source
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.passagetopics import (
    PASSAGE_FLOOR,
    count_awaiting_passages,
    passage_fingerprint,
    passage_topics_for,
)
from meridian_core.search import SearchFilters, search
from meridian_core.sources import upsert_source
from meridian_core.topiclabels import REFERENCE_TEXTS, load_prototypes, topic_name
from worker.retopic import Labeller, render

pytestmark = pytest.mark.usefixtures("require_db")

SPARE = EMBEDDING_DIM - 1
ELSEWHERE = EMBEDDING_DIM - 2


def axis(i: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[i] = 1.0
    return v


def blend(*weights: tuple[int, float]) -> list[float]:
    v = np.zeros(EMBEDDING_DIM)
    for i, w in weights:
        v[i] += w
    return list(v / np.linalg.norm(v))


class AxisEmbedder:
    """Topic prototype → its own axis; reference phrase → ±SPARE."""

    def __init__(self, topics: list[str]) -> None:
        self.expect_model = f"test-{uuid.uuid4().hex[:8]}"
        self.axes = {topic_name(t): i for i, t in enumerate(sorted(topics))}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for phrase in texts:
            if phrase in REFERENCE_TEXTS:
                sign = 1.0 if REFERENCE_TEXTS.index(phrase) % 2 == 0 else -1.0
                out.append([sign * x for x in axis(SPARE)])
                continue
            out.append(axis(self.axes[phrase.split(":", 1)[0]]))
        return out


@pytest.fixture
async def world(session_for):
    sess = await session_for("rw")
    topics = sorted(p.topic for p in await load_prototypes(sess))
    if len(topics) < 3:
        pytest.skip("needs at least three seeded topics")
    start_after = int(await sess.scalar(select(func.coalesce(func.max(Source.source_id), 0))))
    passages_after = int(await sess.scalar(select(func.coalesce(func.max(Chunk.chunk_id), 0))))
    embedder = AxisEmbedder(topics)

    @asynccontextmanager
    async def factory():
        sess.commit = sess.flush
        yield sess

    def labeller() -> Labeller:
        return Labeller(
            embedder,
            session_factory=factory,
            start_after=start_after,
            passages_after=passages_after,
        )

    def ax(topic: str) -> int:
        return embedder.axes[topic_name(topic)]

    yield sess, topics, embedder, labeller, ax
    await sess.rollback()


async def a_document(sess, passages: list[tuple[str, list[float] | None]]) -> tuple[int, list[int]]:
    """A source whose chunks carry the given texts and vectors, in order."""
    source, _ = await upsert_source(
        sess, f"https://p{uuid.uuid4().hex[:12]}.test/doc", checksum=f"sha256:{uuid.uuid4().hex}"
    )
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=text, chunk_index=i) for i, (text, _) in enumerate(passages)],
    )
    await sess.flush()
    ids = list(
        await sess.scalars(
            select(Chunk.chunk_id)
            .where(Chunk.source_id == source.source_id, Chunk.superseded_at.is_(None))
            .order_by(Chunk.chunk_index)
        )
    )
    for chunk_id, (_, vector) in zip(ids, passages, strict=True):
        if vector is not None:
            await sess.execute(
                update(Chunk).where(Chunk.chunk_id == chunk_id).values(embedding=vector)
            )
    return source.source_id, ids


async def row(sess, chunk_id: int) -> ChunkTopics | None:
    sess.expire_all()
    return await sess.get(ChunkTopics, chunk_id)


def word() -> str:
    """A token no other chunk in the database contains, for lexical search."""
    return f"zq{uuid.uuid4().hex[:10]}"


# --------------------------------------------------------------------------
# What a pass writes
# --------------------------------------------------------------------------


async def test_a_passage_gets_the_topic_its_own_vector_earns(world) -> None:
    sess, topics, _, labeller, ax = world
    a = topics[0]
    _, (cid,) = await a_document(sess, [("a passage", axis(ax(a)))])

    stats = await labeller().run(apply=True)

    got = await row(sess, cid)
    assert got.topic_labels == [a]
    assert got.topic_scores[a] == pytest.approx(1.0, abs=1e-3)
    # The passage basis is built on the source basis: one moves the other.
    assert got.topic_basis == passage_fingerprint((await labeller().basis()).fingerprint)
    assert stats.passages.examined >= 1


async def test_at_the_floor_labels_and_just_under_it_is_examined_and_empty(world) -> None:
    """``>=`` at the floor; ``{}`` — a row, not a missing one — just below it."""
    sess, topics, _, labeller, ax = world
    a = topics[0]

    def at(score: float) -> list[float]:
        return blend((ax(a), score), (ELSEWHERE, math.sqrt(1 - score**2)))

    _, (on, under) = await a_document(
        sess, [("at the floor", at(PASSAGE_FLOOR + 0.0005)), ("under it", at(PASSAGE_FLOOR - 0.01))]
    )

    await labeller().run(apply=True)

    assert (await row(sess, on)).topic_labels == [a]
    below = await row(sess, under)
    assert below is not None and below.topic_labels == []
    assert below.topic_scores[a] == pytest.approx(PASSAGE_FLOOR - 0.01, abs=1e-3)


async def test_a_passage_about_nothing_configured_is_rejected(world) -> None:
    sess, *_, labeller, _ = world
    _, (cid,) = await a_document(sess, [("elsewhere", axis(ELSEWHERE))])

    await labeller().run(apply=True)

    assert (await row(sess, cid)).topic_labels == []


async def test_a_listing_is_examined_and_labelled_empty_however_it_scores(world) -> None:
    """Its vector is the average of what it links to, not a claim about any of it."""
    sess, topics, _, labeller, ax = world
    a = topics[0]
    listing = " ".join(f"[Entry {i}](https://example.test/{i})" for i in range(12))
    _, (cid,) = await a_document(sess, [(listing, axis(ax(a)))])

    stats = await labeller().run(apply=True)

    got = await row(sess, cid)
    assert got.topic_labels == []
    # The score is kept, so the threshold can be re-measured later.
    assert got.topic_scores[a] == pytest.approx(1.0, abs=1e-3)
    assert stats.passages.listings >= 1


async def test_an_unembedded_passage_has_no_row(world) -> None:
    """NULL is "nothing examined this"; ``{}`` would claim a vector nobody has."""
    sess, topics, _, labeller, ax = world
    _, (embedded, bare) = await a_document(
        sess, [("embedded", axis(ax(topics[0]))), ("not yet", None)]
    )

    await labeller().run(apply=True)

    assert await row(sess, embedded) is not None
    assert await row(sess, bare) is None


async def test_a_report_run_writes_no_passage_rows(world) -> None:
    sess, topics, _, labeller, ax = world
    _, (cid,) = await a_document(sess, [("report only", axis(ax(topics[0])))])

    stats = await labeller().run(apply=False)

    assert stats.passages.examined >= 1
    assert await row(sess, cid) is None


async def test_the_passage_stage_can_be_skipped(world) -> None:
    sess, topics, _, labeller, ax = world
    sid, (cid,) = await a_document(sess, [("sources only", axis(ax(topics[0])))])

    stats = await labeller().run(apply=True, passages=False)

    assert stats.passages is None
    assert await row(sess, cid) is None
    sess.expire_all()
    assert (await sess.get(Source, sid)).topic_labels == [topics[0]]


# --------------------------------------------------------------------------
# Staleness
# --------------------------------------------------------------------------


async def test_a_labelled_passage_is_not_examined_again(world) -> None:
    sess, topics, _, labeller, ax = world
    await a_document(sess, [("once", axis(ax(topics[0])))])
    await labeller().run(apply=True)

    again = await labeller().run(apply=True)

    assert again.passages.examined == 0


async def test_a_changed_basis_reexamines_every_passage(world) -> None:
    sess, topics, emb, labeller, ax = world
    _, (cid,) = await a_document(sess, [("basis", axis(ax(topics[0])))])
    await labeller().run(apply=True)
    before = (await row(sess, cid)).topic_basis

    emb.expect_model = f"test-{uuid.uuid4().hex[:8]}"
    again = await labeller().run(apply=True)

    assert again.passages.examined >= 1
    assert (await row(sess, cid)).topic_basis not in (None, before)


async def test_a_reembedded_passage_is_stale_and_only_it(world) -> None:
    """A new embedding view means a new vector; the old row describes the old one."""
    sess, topics, _, labeller, ax = world
    a, b = topics[:2]
    _, (moved, kept) = await a_document(sess, [("moved", axis(ax(a))), ("kept", axis(ax(a)))])
    await labeller().run(apply=True)

    await sess.execute(
        update(Chunk).where(Chunk.chunk_id == moved).values(embedding=axis(ax(b)), embedding_view=1)
    )
    again = await labeller().run(apply=True)

    assert again.passages.examined == 1
    assert (await row(sess, moved)).topic_labels == [b]
    assert (await row(sess, moved)).embedding_view == 1
    assert (await row(sess, kept)).topic_labels == [a]


async def test_a_recrawl_labels_the_new_passages_once_they_have_vectors(world) -> None:
    sess, topics, emb, labeller, ax = world
    a, b = topics[:2]
    sid, (old,) = await a_document(sess, [("first version", axis(ax(a)))])
    await labeller().run(apply=True)

    await replace_chunks(sess, sid, [ChunkWrite(text="second version", chunk_index=0)])
    await sess.flush()
    new = await sess.scalar(
        select(Chunk.chunk_id).where(Chunk.source_id == sid, Chunk.superseded_at.is_(None))
    )
    fingerprint = passage_fingerprint((await labeller().basis()).fingerprint)
    # No vector yet: not in the queue, not labelled.
    await labeller().run(apply=True)
    assert await row(sess, new) is None

    await sess.execute(update(Chunk).where(Chunk.chunk_id == new).values(embedding=axis(ax(b))))
    assert await count_awaiting_passages(sess, fingerprint) >= 1
    await labeller().run(apply=True)

    assert (await row(sess, new)).topic_labels == [b]
    # The superseded chunk keeps its row; nothing serving the corpus reads it.
    assert (await row(sess, old)).topic_labels == [a]


# --------------------------------------------------------------------------
# The consumer: a topic filter finds passages in mixed documents
# --------------------------------------------------------------------------


async def mixed(world):
    """A document mostly about topic A with one passage about topic B, plus a
    document about nothing configured. Labelled, and a word to search them by."""
    sess, topics, _, labeller, ax = world
    a, b = topics[:2]
    token = word()
    mostly_a, ids = await a_document(
        sess,
        [
            (f"{token} first part", axis(ax(a))),
            (f"{token} second part", axis(ax(a))),
            (f"{token} the chapter on another subject", axis(ax(b))),
        ],
    )
    _, (off,) = await a_document(sess, [(f"{token} unrelated", axis(ELSEWHERE))])
    await labeller().run(apply=True)
    return token, a, b, mostly_a, ids, off


async def test_the_report_counts_what_the_passage_stage_adds_and_shows_it(world, capsys) -> None:
    """The report is where the next calibration starts, so it has to name the
    passages whose labels their source does not carry."""
    _, _, emb, labeller, _ = world
    _, _, b, _, ids, _ = await mixed(world)
    stats = await labeller().run(apply=False)  # everything is current: nothing to do
    assert stats.passages.examined == 0

    # A new basis puts every passage back in the queue; report only.
    emb.expect_model = f"test-{uuid.uuid4().hex[:8]}"
    stats = await labeller().run(apply=False)

    assert stats.passages.beyond_source[b] >= 1
    assert stats.as_dict()["passage_labels_beyond_source"] >= 1
    render(stats, apply=False, demote_offtopic=False, offtopic_floor=0.3)
    out = capsys.readouterr().out
    assert "Topics per passage" in out
    assert "Labelled with a topic its source does not carry" in out
    assert f"chunk {ids[2]}" in out


async def test_the_mixed_document_carries_one_source_label(world) -> None:
    """The premise: the mean vector hides the chapter."""
    sess, *_ = world
    _, a, b, mostly_a, ids, _ = await mixed(world)
    sess.expire_all()
    assert (await sess.get(Source, mostly_a)).topic_labels == [a]
    assert (await row(sess, ids[2])).topic_labels == [b]


async def test_a_topic_filter_finds_the_passage_its_document_is_not_labelled_with(world) -> None:
    sess, *_ = world
    token, a, b, _, ids, off = await mixed(world)

    result = await search(sess, token, filters=SearchFilters(topics=[b]))

    found = {hit.chunk_id for hit in result.hits}
    assert found == {ids[2]}
    (hit,) = result.hits
    assert hit.topic_labels == [a] and hit.passage_topics == [b]


async def test_the_source_topic_still_matches_every_passage_of_its_document(world) -> None:
    sess, *_ = world
    token, a, _, _, ids, off = await mixed(world)

    result = await search(sess, token, filters=SearchFilters(topics=[a]))

    assert {hit.chunk_id for hit in result.hits} == set(ids)


async def test_all_topics_finds_where_they_meet(world) -> None:
    """`B-72`: the chapter on B inside a document on A is where A and B meet —
    the rest of the document is about A only."""
    sess, *_ = world
    token, a, b, _, ids, _ = await mixed(world)

    both = await search(sess, token, filters=SearchFilters(topics=[a, b], topics_all=True))
    either = await search(sess, token, filters=SearchFilters(topics=[a, b]))

    assert {hit.chunk_id for hit in both.hits} == {ids[2]}
    assert set(ids) <= {hit.chunk_id for hit in either.hits}


async def test_all_topics_with_one_topic_is_the_ordinary_filter(world) -> None:
    sess, *_ = world
    token, a, _, _, ids, _ = await mixed(world)

    one = await search(sess, token, filters=SearchFilters(topics=[a], topics_all=True))

    assert {hit.chunk_id for hit in one.hits} == set(ids)


async def test_all_topics_never_matches_an_unexamined_source_by_accident(world) -> None:
    """NULL labels coalesce to empty, never to "everything"."""
    sess, topics, *_ = world
    token, a, b, _, _, off = await mixed(world)
    third = topics[2]

    result = await search(sess, token, filters=SearchFilters(topics=[a, b, third], topics_all=True))

    assert result.hits == []


async def test_overlaps_count_sources_by_exact_combination(world) -> None:
    from meridian_core.topicoverlaps import topic_overlaps

    sess, topics, *_ = world
    a, b = topics[0], topics[1]
    before = await topic_overlaps(sess)
    pair = next((o.sources for o in before.overlaps if o.topics == sorted([a, b])), 0)

    source_id, _ = await a_document(sess, [("overlap probe", None)])
    source = await sess.get(Source, source_id)
    source.topic_labels = [b, a]  # stored in any order; counted sorted
    await sess.flush()

    after = await topic_overlaps(sess)
    counts = {tuple(o.topics): o.sources for o in after.overlaps}
    assert counts[tuple(sorted([a, b]))] == pair + 1
    assert after.labelled_sources == before.labelled_sources + 1
    assert all(o.topics == sorted(o.topics) for o in after.overlaps)


async def test_overlaps_leave_out_junk_and_unlabelled_sources(world) -> None:
    from meridian_core.topicoverlaps import topic_overlaps

    sess, topics, *_ = world
    before = (await topic_overlaps(sess)).labelled_sources
    junk_id, _ = await a_document(sess, [("junk probe", None)])
    empty_id, _ = await a_document(sess, [("empty probe", None)])
    junk = await sess.get(Source, junk_id)
    junk.topic_labels, junk.retention_tier = [topics[0]], "junk"
    (await sess.get(Source, empty_id)).topic_labels = []
    await sess.flush()

    assert (await topic_overlaps(sess)).labelled_sources == before


async def test_a_topic_filter_rejects_passages_about_nothing_it_named(world) -> None:
    sess, topics, *_ = world
    token, _, _, _, ids, off = await mixed(world)
    third = topics[2]

    assert (await search(sess, token, filters=SearchFilters(topics=[third]))).hits == []
    unfiltered = {hit.chunk_id for hit in (await search(sess, token)).hits}
    assert off in unfiltered and set(ids) <= unfiltered


async def test_a_hit_says_null_for_an_unexamined_passage_and_empty_for_an_offtopic_one(
    world,
) -> None:
    sess, *_ = world
    token, _, _, _, _, off = await mixed(world)
    _, (bare,) = await a_document(sess, [(f"{token} unembedded", None)])

    hits = {hit.chunk_id: hit for hit in (await search(sess, token)).hits}

    assert hits[off].passage_topics == []
    assert hits[bare].passage_topics is None


async def test_evidence_panels_read_the_same_labels_search_does(world) -> None:
    """The node panel and the evidence route hydrate chunks through
    `passage_topics_for`; an unexamined chunk is absent (None), never ``[]``."""
    sess, *_ = world
    _, b, _, ids, off = (await mixed(world))[1:]
    _, (bare,) = await a_document(sess, [("unembedded", None)])

    got = await passage_topics_for(sess, [ids[2], off, bare])

    assert got == {ids[2]: [b], off: []}
    assert await passage_topics_for(sess, []) == {}


# --------------------------------------------------------------------------
# The table itself
# --------------------------------------------------------------------------


async def test_the_read_only_role_reads_passage_labels_and_cannot_write_them(session_for) -> None:
    """Explore search runs on the read-only role and now reads this table; a
    missing grant would fail every topic-filtered search, not just this one."""
    sess = await session_for("ro")
    assert await sess.scalar(select(func.count()).select_from(ChunkTopics)) is not None
    with pytest.raises(Exception) as exc:
        await sess.execute(text("DELETE FROM chunk_topics"))
    assert "permission denied" in str(exc.value).lower()


async def test_a_row_for_no_chunk_is_refused(session_for) -> None:
    sess = await session_for("rw")
    missing = int(await sess.scalar(select(func.coalesce(func.max(Chunk.chunk_id), 0)))) + 10_000
    sess.add(ChunkTopics(chunk_id=missing, topic_labels=[], topic_scores={}, topic_basis="t"))
    with pytest.raises(IntegrityError):
        await sess.flush()


async def test_null_labels_are_refused_because_absence_is_the_row(world) -> None:
    """NULL-vs-``{}`` lives in whether the row exists; a NULL array would be a
    third state that nothing reads correctly."""
    sess, *_ = world
    _, (cid,) = await a_document(sess, [("x", None)])
    with pytest.raises(IntegrityError):
        await sess.execute(
            text(
                "INSERT INTO chunk_topics (chunk_id, topic_labels, topic_scores, topic_basis) "
                "VALUES (:c, NULL, '{}', 't')"
            ),
            {"c": cid},
        )


async def test_deleting_a_chunk_deletes_its_row(world) -> None:
    sess, topics, _, labeller, ax = world
    _, (cid,) = await a_document(sess, [("doomed", axis(ax(topics[0])))])
    await labeller().run(apply=True)
    assert await row(sess, cid) is not None

    await sess.execute(delete(Chunk).where(Chunk.chunk_id == cid))

    assert await row(sess, cid) is None
