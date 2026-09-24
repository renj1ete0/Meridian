"""Diversity seeds from a constructed graph, against Postgres (task P5-05, spec §7.4).

Everything happens inside one transaction that is rolled back: the seeding
pass reads the rows through the same session, so nothing is committed to a
database somebody else is using.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from meridian_core import diversity
from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import (
    AttributeDefinition,
    AttributeValue,
    Chunk,
    Edge,
    Entity,
    QueueTask,
    TopicConfig,
)
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.sources import upsert_source
from worker.seedsearch import MAX_PENDING, run_once

pytestmark = pytest.mark.usefixtures("require_db")


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


class Graph:
    """A small graph of this test's own, filed under a topic of its own."""

    def __init__(self, sess, marker: str, topic: str) -> None:
        self.sess = sess
        self.marker = marker
        self.topic = topic
        self.n = 0

    async def source(self, tier: str, *, labels: list[str] | None = None) -> int:
        """One source of ``tier`` with one chunk; returns the chunk id."""
        self.n += 1
        source, _ = await upsert_source(
            self.sess,
            f"https://{self.marker}-{self.n}.test/doc",
            checksum=f"sha256:{uuid.uuid4().hex}",
            source_tier=tier,
        )
        source.topic_labels = labels if labels is not None else [self.topic]
        await replace_chunks(
            self.sess,
            source.source_id,
            [ChunkWrite(text=f"{self.marker} passage {self.n}.", chunk_index=0)],
        )
        await self.sess.flush()
        return (
            await self.sess.scalars(
                select(Chunk.chunk_id).where(Chunk.source_id == source.source_id)
            )
        ).one()

    async def node(self, name: str, node_type: str = "concept", **kw) -> Entity:
        e = Entity(canonical_name=f"{name} {self.marker}", node_type=node_type, **kw)
        self.sess.add(e)
        await self.sess.flush()
        return e

    async def edge(self, a: Entity, b: Entity, chunks: list[int]) -> None:
        self.sess.add(
            Edge(
                from_node=a.entity_id,
                to_node=b.entity_id,
                relation_type="relates_to",
                supporting_chunk_ids=chunks,
            )
        )
        await self.sess.flush()

    async def backed_by(self, subject: Entity, tiers: list[str]) -> None:
        """Give ``subject`` one source per entry of ``tiers``, through edges to a hub
        whose own name is too short to search for."""
        hub = await self.node("hb", "concept")
        hub.canonical_name = "hb"  # too short to be searched: only `subject` is a candidate
        for tier in tiers:
            await self.edge(subject, hub, [await self.source(tier)])


@pytest.fixture
async def graph(sess) -> Graph:
    marker = f"dv{uuid.uuid4().hex[:8]}"
    topic = f"divtopic-{uuid.uuid4().hex[:6]}"
    for row in await sess.scalars(select(TopicConfig)):
        row.status = "paused"
    sess.add(TopicConfig(topic=topic, weight=0.1, floor=0.0, ceiling=1.0, status="active"))
    await sess.flush()
    return Graph(sess, marker, topic)


async def graph_rows(sess, topic: str) -> list[QueueTask]:
    return list(
        await sess.scalars(
            select(QueueTask).where(
                QueueTask.topic == topic,
                QueueTask.seed_mechanism.in_(["tier_imbalance", "distant_walk"]),
            )
        )
    )


async def test_a_single_tier_node_is_queued_counter_seeds_with_its_mechanism(sess, graph) -> None:
    lopsided = await graph.node("lopsided subject", topic_labels=[graph.topic])
    await graph.backed_by(lopsided, ["government", "government", "government"])

    run = await run_once(write=True, seed=1, session_factory=factory(sess))

    rows = await graph_rows(sess, graph.topic)
    tier_rows = [r for r in rows if r.seed_mechanism == "tier_imbalance"]
    assert tier_rows
    assert all(lopsided.canonical_name in r.url_or_query for r in tier_rows)
    assert {(r.task_type, r.seed_source) for r in tier_rows} == {("query", "diversity")}
    assert len(tier_rows) <= diversity.PER_NODE
    # The report says which node and why.
    reasons = [g.reason for g in run.graph if g.mechanism == "tier_imbalance"]
    assert reasons and all("3 sources" in r and "government" in r for r in reasons)


async def test_balanced_and_thin_nodes_get_no_counter_seed(sess, graph) -> None:
    balanced = await graph.node("balanced subject", topic_labels=[graph.topic])
    await graph.backed_by(balanced, ["government", "press", "peer_reviewed"])
    thin = await graph.node("thin subject", topic_labels=[graph.topic])
    await graph.backed_by(thin, ["government", "government"])

    run = await run_once(write=True, seed=1, session_factory=factory(sess))

    assert not [g for g in run.graph if g.mechanism == "tier_imbalance"]
    assert not [
        r for r in await graph_rows(sess, graph.topic) if r.seed_mechanism == "tier_imbalance"
    ]


async def test_a_copy_of_a_source_is_not_a_second_voice(sess, graph) -> None:
    subject = await graph.node("copied subject", topic_labels=[graph.topic])
    await graph.backed_by(subject, ["government", "government", "government"])
    chunk_sources = list(
        await sess.scalars(
            select(Chunk.source_id)
            .join(Edge, Chunk.chunk_id == Edge.supporting_chunk_ids.any_())
            .where(Edge.from_node == subject.entity_id)
        )
    )
    await sess.execute(
        text(
            "UPDATE sources SET duplicate_of = :a, duplicate_reason = 'same text' "
            "WHERE source_id = :b"
        ),
        {"a": chunk_sources[0], "b": chunk_sources[1]},
    )

    nodes = await diversity.graph_inputs(sess, active=[graph.topic])
    mine = next(n for n in nodes if n.entity_id == subject.entity_id)
    assert mine.tiers == {"government": 2}
    assert diversity.tier_gap(mine) is None


async def test_attribute_evidence_counts_and_merged_nodes_are_skipped(sess, graph) -> None:
    subject = await graph.node("attributed subject", topic_labels=[graph.topic])
    merged = await graph.node("merged subject", topic_labels=[graph.topic])
    merged.redirects_to = subject.entity_id
    definition = AttributeDefinition(name=f"{graph.marker}-attr", scope="global")
    sess.add(definition)
    await sess.flush()
    sess.add(
        AttributeValue(
            entity_id=subject.entity_id,
            attribute_id=definition.attribute_id,
            value="x",
            supporting_chunk_ids=[await graph.source("press") for _ in range(3)],
        )
    )
    await sess.flush()

    nodes = {n.entity_id: n for n in await diversity.graph_inputs(sess, active=[graph.topic])}
    assert nodes[subject.entity_id].tiers == {"press": 3}
    assert merged.entity_id not in nodes


async def test_a_node_without_labels_is_filed_under_its_sources_topic(sess, graph) -> None:
    subject = await graph.node("unlabelled subject")
    await graph.backed_by(subject, ["government"] * 3)
    stray = await graph.node("stray subject")
    hub = await graph.node("hb2")
    await graph.edge(stray, hub, [await graph.source("press", labels=["not-a-live-topic"])])

    nodes = {n.entity_id: n for n in await diversity.graph_inputs(sess, active=[graph.topic])}
    assert nodes[subject.entity_id].topic == graph.topic
    assert nodes[stray.entity_id].topic is None


async def test_a_report_writes_nothing_and_a_second_run_repeats_nothing(sess, graph) -> None:
    subject = await graph.node("repeated subject", topic_labels=[graph.topic])
    await graph.backed_by(subject, ["peer_reviewed"] * 4)

    planned = await run_once(write=False, seed=3, session_factory=factory(sess))
    assert planned.graph
    assert await graph_rows(sess, graph.topic) == []

    await run_once(write=True, seed=3, session_factory=factory(sess))
    await run_once(write=True, seed=3, session_factory=factory(sess))
    texts = [r.url_or_query.lower() for r in await graph_rows(sess, graph.topic)]
    assert texts and len(texts) == len(set(texts))


async def test_a_backlogged_topic_gets_no_graph_queries(sess, graph) -> None:
    subject = await graph.node("backlogged subject", topic_labels=[graph.topic])
    await graph.backed_by(subject, ["government"] * 3)
    for i in range(MAX_PENDING):
        sess.add(
            QueueTask(
                url_or_query=f"waiting {i} {graph.marker}",
                topic=graph.topic,
                task_type="query",
                seed_source="diversity",
            )
        )
    await sess.flush()

    run = await run_once(write=True, seed=1, session_factory=factory(sess))

    assert not [g for g in run.graph if g.topic == graph.topic]


async def test_caps_hold_across_many_lopsided_nodes(sess, graph) -> None:
    for i in range(8):
        subject = await graph.node(f"capped subject {i}", topic_labels=[graph.topic])
        await graph.backed_by(subject, ["government"] * 3)

    run = await run_once(write=True, seed=1, session_factory=factory(sess))

    mine = [g for g in run.graph if g.topic == graph.topic]
    assert len([g for g in mine if g.mechanism == "tier_imbalance"]) == diversity.TIER_PER_RUN
    assert len([g for g in mine if g.mechanism == "distant_walk"]) <= diversity.WALKS_PER_RUN


async def test_a_leaf_is_walked_and_distance_is_from_the_recent_crawl(sess, graph) -> None:
    near = await graph.node("near subject", topic_labels=[graph.topic])
    far = await graph.node("far subject", topic_labels=[graph.topic])
    along, against = [0.0] * EMBEDDING_DIM, [0.0] * EMBEDDING_DIM
    along[0], against[1] = 1.0, 1.0
    near.embedding, far.embedding = along, against
    for _ in range(3):
        chunk = await sess.get(Chunk, await graph.source("press"))
        chunk.embedding = along
    await sess.flush()

    nodes = {
        n.entity_id: n for n in await diversity.graph_inputs(sess, active=[graph.topic], recent=3)
    }
    assert nodes[near.entity_id].distance == pytest.approx(0.0, abs=1e-6)
    assert nodes[far.entity_id].distance == pytest.approx(1.0, abs=1e-6)

    run = await run_once(write=True, seed=5, session_factory=factory(sess))
    walks = [r for r in await graph_rows(sess, graph.topic) if r.seed_mechanism == "distant_walk"]
    # Both are leaves (no edges), so both are far; the cap is what limits them.
    assert 1 <= len(walks) <= diversity.WALKS_PER_RUN
    assert {g.mechanism for g in run.graph if g.topic == graph.topic} == {"distant_walk"}


async def test_topic_seeds_record_their_mechanism_too(sess, graph) -> None:
    await run_once(write=True, per_topic=50, seed=1, session_factory=factory(sess))

    rows = list(await sess.scalars(select(QueueTask).where(QueueTask.topic == graph.topic)))
    counter = [r for r in rows if r.url_or_query.startswith("criticism of")]
    assert counter and {r.seed_mechanism for r in counter} == {"counter_seed"}
    plain = [r for r in rows if r.url_or_query == diversity.topic_words(graph.topic)]
    assert plain and {r.seed_mechanism for r in plain} == {None}


async def test_the_column_refuses_an_unknown_mechanism(sess) -> None:
    """The database refuses it, not only the ORM (raw SQL skips the ORM's check)."""
    with pytest.raises(IntegrityError):
        await sess.execute(
            text(
                "INSERT INTO queue (url_or_query, task_type, seed_source, seed_mechanism) "
                "VALUES (:q, 'query', 'diversity', 'made_up')"
            ),
            {"q": f"q {uuid.uuid4().hex}"},
        )


async def test_the_column_accepts_every_mechanism_the_writers_use(sess) -> None:
    from meridian_core.searchseeds import MECHANISM_BY_KIND

    for mechanism in {*MECHANISM_BY_KIND.values(), "tier_imbalance", "distant_walk"}:
        await sess.execute(
            text(
                "INSERT INTO queue (url_or_query, task_type, seed_source, seed_mechanism) "
                "VALUES (:q, 'query', 'diversity', :m)"
            ),
            {"q": f"q {uuid.uuid4().hex}", "m": mechanism},
        )
