"""The content labeller against Postgres (task P2-21).

The pure half — `decide`, `is_offtopic` — is tested in `test_topics_on_sources`.
This file is about what only the database can answer: which sources the queue
picks up, what a pass writes to them, when a label goes stale, and that a
demotion happens only when both gates are open.

**The vectors are constructed, not embedded.** A fake embedder gives every
topic its own axis and the reference phrases alternate between one spare axis
and its negation, so their mean is zero and a score is exactly the cosine the
test set up. That makes the thresholds testable at their edges rather than
"somewhere near" them.
"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from contextlib import asynccontextmanager

import numpy as np
import pytest
from sqlalchemy import func, select, update

from meridian_core.chunks import SAMPLE_HEAD, SAMPLE_STRIDE, ChunkWrite, in_sample, replace_chunks
from meridian_core.models import Chunk, Source
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.sources import upsert_source
from meridian_core.topiclabels import (
    LABEL_FLOOR,
    OFFTOPIC_FLOOR,
    REFERENCE_TEXTS,
    TRIAGE_FLOOR,
    load_prototypes,
    topic_name,
)
from worker.retopic import Labeller, NoTopics

pytestmark = pytest.mark.usefixtures("require_db")

#: The axis the reference phrases sit on, far from any topic's.
SPARE = EMBEDDING_DIM - 1
#: An axis no topic owns: a page pointing here is about nothing configured.
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
        # A unique model name makes a unique basis fingerprint, so nothing this
        # test labels can be confused with a label the dev corpus carries.
        self.expect_model = f"test-{uuid.uuid4().hex[:8]}"
        self.axes = {topic_name(t): i for i, t in enumerate(sorted(topics))}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            if text in REFERENCE_TEXTS:
                sign = 1.0 if REFERENCE_TEXTS.index(text) % 2 == 0 else -1.0
                out.append([sign * x for x in axis(SPARE)])
                continue
            name = text.split(":", 1)[0]
            if name not in self.axes:
                raise AssertionError(f"the labeller embedded something unexpected: {text!r}")
            out.append(axis(self.axes[name]))
        return out


@pytest.fixture
async def world(session_for):
    """The test's transaction, the configured topics, and a cursor past the dev corpus."""
    sess = await session_for("rw")
    topics = [p.topic for p in await load_prototypes(sess)]
    if len(topics) < 2:
        pytest.skip("needs at least two seeded topics")
    start_after = int(await sess.scalar(select(func.coalesce(func.max(Source.source_id), 0))))
    embedder = AxisEmbedder(topics)

    @asynccontextmanager
    async def factory():
        # Commits become flushes so the whole test is one transaction the
        # fixture can discard; nothing here relies on a real commit.
        sess.commit = sess.flush
        yield sess

    def labeller() -> Labeller:
        return Labeller(embedder, session_factory=factory, start_after=start_after)

    yield sess, topics, embedder, labeller
    await sess.rollback()


async def a_source(sess, vector: list[float] | None, *, chunks: int = 1) -> int:
    source, _ = await upsert_source(
        sess, f"https://t{uuid.uuid4().hex[:12]}.test/p", checksum=f"sha256:{uuid.uuid4().hex}"
    )
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=f"passage {i}", chunk_index=i) for i in range(chunks)],
    )
    await sess.flush()
    if vector is not None:
        await sess.execute(
            update(Chunk).where(Chunk.source_id == source.source_id).values(embedding=vector)
        )
    return source.source_id


async def labels_of(sess, source_id: int):
    sess.expire_all()
    return await sess.get(Source, source_id)


# --------------------------------------------------------------------------
# What a pass writes
# --------------------------------------------------------------------------


async def test_a_page_about_one_topic_gets_that_topic_only(world) -> None:
    sess, topics, emb, labeller = world
    first = sorted(topics)[0]
    sid = await a_source(sess, axis(emb.axes[topic_name(first)]))

    await labeller().run(apply=True)

    row = await labels_of(sess, sid)
    assert row.topic_labels == [first]
    assert row.topic_scores[first] == pytest.approx(1.0, abs=1e-3)
    assert row.topics_examined_at is not None and row.topic_basis


async def test_a_page_about_two_topics_gets_both_best_first(world) -> None:
    sess, topics, emb, labeller = world
    a, b = sorted(topics)[:2]
    # b slightly stronger, inside the margin: both, b first.
    sid = await a_source(
        sess, blend((emb.axes[topic_name(a)], 1.0), (emb.axes[topic_name(b)], 1.02))
    )

    await labeller().run(apply=True)

    assert (await labels_of(sess, sid)).topic_labels == [b, a]


async def test_a_second_topic_outside_the_margin_is_not_a_label(world) -> None:
    sess, topics, emb, labeller = world
    a, b = sorted(topics)[:2]
    # cos to b ≈ 0.6, to a ≈ 0.8: both above the floor, 0.2 apart.
    sid = await a_source(
        sess, blend((emb.axes[topic_name(a)], 0.8), (emb.axes[topic_name(b)], 0.6))
    )

    await labeller().run(apply=True)

    assert (await labels_of(sess, sid)).topic_labels == [a]


async def test_a_page_just_under_the_floor_is_examined_and_unlabelled(world) -> None:
    """``{}``, not NULL: it was read. And not off-topic: it is above that floor."""
    sess, topics, emb, labeller = world
    a = sorted(topics)[0]
    target = LABEL_FLOOR - 0.01
    sid = await a_source(
        sess, blend((emb.axes[topic_name(a)], target), (ELSEWHERE, math.sqrt(1 - target**2)))
    )

    stats = await labeller().run(apply=True)

    row = await labels_of(sess, sid)
    assert row.topic_labels == []
    assert row.topic_scores[a] == pytest.approx(target, abs=1e-3)
    assert sid not in {s for s, _ in stats.offtopic}


async def test_a_page_about_nothing_configured_is_empty_and_an_offtopic_candidate(world) -> None:
    sess, _, _, labeller = world
    sid = await a_source(sess, axis(ELSEWHERE))

    stats = await labeller().run(apply=True)

    assert (await labels_of(sess, sid)).topic_labels == []
    assert sid in {s for s, _ in stats.offtopic}


async def test_a_source_with_no_embedded_text_stays_null(world) -> None:
    """Nothing examined it; ``{}`` would be a claim about text nobody has read."""
    sess, _, _, labeller = world
    sid = await a_source(sess, None)

    await labeller().run(apply=True)

    row = await labels_of(sess, sid)
    assert row.topic_labels is None and row.topics_examined_at is None


async def test_a_half_embedded_source_waits(world) -> None:
    sess, topics, emb, labeller = world
    sid = await a_source(sess, axis(emb.axes[topic_name(sorted(topics)[0])]), chunks=2)
    first = await sess.scalar(select(func.min(Chunk.chunk_id)).where(Chunk.source_id == sid))
    await sess.execute(update(Chunk).where(Chunk.chunk_id == first).values(embedding=None))

    await labeller().run(apply=True)

    assert (await labels_of(sess, sid)).topic_labels is None


async def test_a_report_run_writes_nothing(world) -> None:
    sess, topics, emb, labeller = world
    sid = await a_source(sess, axis(emb.axes[topic_name(sorted(topics)[0])]))

    stats = await labeller().run(apply=False)

    assert stats.examined >= 1
    assert (await labels_of(sess, sid)).topic_labels is None


# --------------------------------------------------------------------------
# Staleness
# --------------------------------------------------------------------------


async def test_a_labelled_source_is_not_examined_again(world) -> None:
    sess, topics, emb, labeller = world
    await a_source(sess, axis(emb.axes[topic_name(sorted(topics)[0])]))
    await labeller().run(apply=True)

    again = await labeller().run(apply=True)

    assert again.examined == 0


async def test_a_recrawl_makes_a_source_stale(world) -> None:
    sess, topics, emb, labeller = world
    a, b = sorted(topics)[:2]
    sid = await a_source(sess, axis(emb.axes[topic_name(a)]))
    await labeller().run(apply=True)

    # The page was rewritten about something else.
    await replace_chunks(sess, sid, [ChunkWrite(text="rewritten", chunk_index=0)])
    await sess.flush()
    await sess.execute(
        update(Chunk)
        .where(Chunk.source_id == sid, Chunk.superseded_at.is_(None))
        .values(
            embedding=axis(emb.axes[topic_name(b)]),
            created_at=func.clock_timestamp() + dt.timedelta(hours=1),
        )
    )
    await labeller().run(apply=True)

    assert (await labels_of(sess, sid)).topic_labels == [b]


async def test_a_changed_basis_reexamines_everything(world) -> None:
    sess, topics, emb, labeller = world
    sid = await a_source(sess, axis(emb.axes[topic_name(sorted(topics)[0])]))
    await labeller().run(apply=True)

    before = (await labels_of(sess, sid)).topic_basis
    emb.expect_model = f"test-{uuid.uuid4().hex[:8]}"
    again = await labeller().run(apply=True)

    assert again.examined >= 1
    assert (await labels_of(sess, sid)).topic_basis not in (None, before)


async def test_no_topics_is_refused_rather_than_labelling_everything_empty(
    world, monkeypatch
) -> None:
    sess, _, _, labeller = world
    sid = await a_source(sess, axis(ELSEWHERE))

    async def none(_sess):
        return []

    monkeypatch.setattr("worker.retopic.load_prototypes", none)
    with pytest.raises(NoTopics):
        await labeller().run(apply=True)
    assert (await labels_of(sess, sid)).topic_labels is None


# --------------------------------------------------------------------------
# Demotion: two gates, and a floor that must sit below the label floor
# --------------------------------------------------------------------------


async def test_demotion_needs_apply_as_well(world) -> None:
    sess, _, _, labeller = world
    sid = await a_source(sess, axis(ELSEWHERE))

    stats = await labeller().run(apply=False, demote_offtopic=True)

    assert sid in {s for s, _ in stats.offtopic}
    assert (await labels_of(sess, sid)).retention_tier != "junk"


async def test_demotion_is_off_by_default(world) -> None:
    sess, _, _, labeller = world
    sid = await a_source(sess, axis(ELSEWHERE))

    await labeller().run(apply=True)

    assert (await labels_of(sess, sid)).retention_tier != "junk"


async def test_demotion_with_both_gates_marks_only_offtopic_sources_junk(world) -> None:
    sess, topics, emb, labeller = world
    off = await a_source(sess, axis(ELSEWHERE))
    on = await a_source(sess, axis(emb.axes[topic_name(sorted(topics)[0])]))

    stats = await labeller().run(apply=True, demote_offtopic=True)

    assert (await labels_of(sess, off)).retention_tier == "junk"
    assert (await labels_of(sess, on)).retention_tier != "junk"
    assert stats.demoted >= 1
    # Nothing was deleted: the tier is the decision, the sweep the deletion.
    assert (
        await sess.scalar(select(func.count()).select_from(Chunk).where(Chunk.source_id == off))
        == 1
    )


@pytest.mark.parametrize("floor", [LABEL_FLOOR, LABEL_FLOOR + 0.1, -0.1])
async def test_an_offtopic_floor_outside_its_range_is_refused(world, floor) -> None:
    *_, labeller = world
    with pytest.raises(ValueError):
        await labeller().run(apply=True, demote_offtopic=True, offtopic_floor=floor)


def test_the_offtopic_floor_sits_below_the_label_floor() -> None:
    assert 0 < OFFTOPIC_FLOOR < LABEL_FLOOR


# --------------------------------------------------------------------------
# A long document labelled from its sample, then from the whole (`B-89`)
# --------------------------------------------------------------------------

LONG = SAMPLE_HEAD + 4 * SAMPLE_STRIDE + 3


async def long_source(sess, sample: list[float], rest: list[float] | None) -> int:
    """A long source whose sample carries one vector and whose rest another,
    or none yet."""
    sid = await a_source(sess, sample, chunks=LONG)
    await sess.execute(
        update(Chunk).where(Chunk.source_id == sid, ~in_sample()).values(embedding=rest)
    )
    return sid


async def test_a_long_document_is_labelled_from_its_sample(world) -> None:
    sess, topics, emb, labeller = world
    a = sorted(topics)[0]
    sid = await long_source(sess, axis(emb.axes[topic_name(a)]), None)

    stats = await labeller().run(apply=True)

    row = await labels_of(sess, sid)
    assert row.topic_labels == [a]
    assert row.topic_sample_best == pytest.approx(1.0, abs=1e-3)
    assert stats.sampled >= 1


async def test_a_sample_still_embedding_waits(world) -> None:
    sess, topics, emb, labeller = world
    sid = await long_source(sess, axis(emb.axes[topic_name(sorted(topics)[0])]), None)
    # The sample's last passage, far from its opening.
    last_in_sample = (LONG - 1) // SAMPLE_STRIDE * SAMPLE_STRIDE
    await sess.execute(
        update(Chunk)
        .where(Chunk.source_id == sid, Chunk.chunk_index == last_in_sample)
        .values(embedding=None)
    )

    await labeller().run(apply=True)

    assert (await labels_of(sess, sid)).topics_examined_at is None


async def test_a_labelled_sample_is_not_read_again_while_the_rest_waits(world) -> None:
    sess, topics, emb, labeller = world
    await long_source(sess, axis(emb.axes[topic_name(sorted(topics)[0])]), None)
    await labeller().run(apply=True)

    again = await labeller().run(apply=True)

    assert again.examined == 0


async def test_the_whole_text_replaces_the_samples_labels(world) -> None:
    """The sample said one topic; the whole document, read once embedded, says
    another — and the whole is the answer that stands."""
    sess, topics, emb, labeller = world
    a, b = sorted(topics)[:2]
    sid = await long_source(sess, axis(emb.axes[topic_name(a)]), None)
    await labeller().run(apply=True)
    assert (await labels_of(sess, sid)).topic_labels == [a]

    await sess.execute(
        update(Chunk)
        .where(Chunk.source_id == sid, ~in_sample())
        .values(embedding=axis(emb.axes[topic_name(b)]))
    )
    stats = await labeller().run(apply=True)

    row = await labels_of(sess, sid)
    assert row.topic_labels == [b], "the rest outnumbers the sample"
    assert row.topic_sample_best is None
    assert stats.sampled == 0
    assert (await labeller().run(apply=True)).examined == 0


async def test_a_short_document_is_never_marked_sampled(world) -> None:
    sess, topics, emb, labeller = world
    sid = await a_source(sess, axis(emb.axes[topic_name(sorted(topics)[0])]), chunks=SAMPLE_HEAD)

    await labeller().run(apply=True)

    assert (await labels_of(sess, sid)).topic_sample_best is None


async def test_a_sample_is_never_demoted(world) -> None:
    """Junk is never embedded, so junking on a sample would stop the rest
    from ever being read."""
    sess, _, _, labeller = world
    sid = await long_source(sess, axis(ELSEWHERE), None)

    stats = await labeller().run(apply=True, demote_offtopic=True)

    row = await labels_of(sess, sid)
    assert row.topic_sample_best is not None and row.topic_sample_best < TRIAGE_FLOOR
    assert row.retention_tier != "junk"
    assert sid not in {s for s, _ in stats.offtopic}
    assert stats.sampled_held >= 1


def test_the_triage_line_sits_between_the_two_floors() -> None:
    """Under the label floor, so a sample's error does not hold back labelled
    documents; over the off-topic floor, or it would hold back almost nothing."""
    assert OFFTOPIC_FLOOR < TRIAGE_FLOOR < LABEL_FLOOR
