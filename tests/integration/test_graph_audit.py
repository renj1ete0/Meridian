"""The graph audit (task `P7-10`, spec §14.1), against a real Postgres.

§14.1: "Read 30 random edges monthly, count how many are wrong", and "sample merges …;
conflation is invisible once done, so it must be looked for deliberately". What matters here:
the sample is drawn from the window and nothing else, the same seed draws the same sample, a
grade changes the sample and never the graph, precision leaves `unsure` out, and what must
not be recorded is refused by the database as well as by the code.

Every row is written at an instant no real row is near (2001, 2099), and the session is rolled
back, so the dev database's own graph never reaches a count.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import uuid

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from api.main import create_app
from meridian_core import graphaudit
from meridian_core.annotations import HUMAN
from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.db import dispose_engines
from meridian_core.models import (
    Chunk,
    Edge,
    Entity,
    GraphAuditDraw,
    GraphAuditSample,
    MergeLog,
    Notification,
)
from meridian_core.resolution import merge, reverse
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")

UTC = dt.UTC
START = dt.datetime(2001, 3, 1, tzinfo=UTC)
NOW = dt.datetime(2001, 4, 1, tzinfo=UTC)
INSIDE = dt.datetime(2001, 3, 15, tzinfo=UTC)
BEFORE = dt.datetime(2001, 2, 1, tzinfo=UTC)


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


async def node(sess, name: str, node_type: str = "concept", chunks=()) -> Entity:
    row = Entity(
        canonical_name=f"{name} {uuid.uuid4().hex[:8]}",
        node_type=node_type,
        supporting_chunk_ids=list(chunks),
    )
    sess.add(row)
    await sess.flush()
    return row


async def passages(sess, *texts: str) -> list[int]:
    source, _ = await upsert_source(
        sess, f"https://audit.example.test/{uuid.uuid4().hex}", checksum="sha256:audit"
    )
    await replace_chunks(
        sess, source.source_id, [ChunkWrite(text=t, chunk_index=i) for i, t in enumerate(texts)]
    )
    return list(
        await sess.scalars(
            select(Chunk.chunk_id)
            .where(Chunk.source_id == source.source_id)
            .order_by(Chunk.chunk_index)
        )
    )


async def an_edge(sess, *, at: dt.datetime, chunks=(1,), **over) -> Edge:
    a, b = await node(sess, "from"), await node(sess, "to")
    row = Edge(
        from_node=a.entity_id,
        to_node=b.entity_id,
        relation_type=over.pop("relation_type", "funds"),
        supporting_chunk_ids=list(chunks),
        produced_by=over.pop("produced_by", "agent-small"),
        model=over.pop("model", "model-a"),
        quality_tier=over.pop("quality_tier", 1),
        created_at=at,
        **over,
    )
    sess.add(row)
    await sess.flush()
    return row


async def a_merge(sess, *, at: dt.datetime, decided_by: str = "user") -> MergeLog:
    source, target = await node(sess, "twin"), await node(sess, "twin")
    entry = await merge(sess, source.entity_id, target.entity_id, decided_by=decided_by)
    entry.at = at
    await sess.flush()
    return entry


async def samples_of(sess, draw_id: int) -> list[GraphAuditSample]:
    return list(
        await sess.scalars(
            select(GraphAuditSample)
            .where(GraphAuditSample.draw_id == draw_id)
            .order_by(GraphAuditSample.position)
        )
    )


async def test_a_draw_samples_the_window_and_nothing_else(sess) -> None:
    chunk_ids = await passages(sess, "The agency funds the programme.", "A second passage.")
    inside = [await an_edge(sess, at=INSIDE, chunks=chunk_ids) for _ in range(3)]
    await an_edge(sess, at=BEFORE)
    await an_edge(sess, at=NOW)  # the window is half-open: `now` itself is outside
    await an_edge(sess, at=INSIDE, produced_by=HUMAN, relation_type="annotates")
    kept = await a_merge(sess, at=INSIDE)
    undone = await a_merge(sess, at=INSIDE)
    await reverse(sess, undone.merge_id, reversed_by="user")

    row = await graphaudit.draw(sess, now=NOW, since=START, seed="fixed")
    assert row is not None
    assert (row.edge_population, row.merge_population, row.merges_reversed) == (3, 1, 1)
    assert (row.window_start, row.window_end) == (START, NOW)

    drawn = await samples_of(sess, row.draw_id)
    assert {s.item_id for s in drawn if s.kind == "edge"} == {e.edge_id for e in inside}
    assert [s.item_id for s in drawn if s.kind == "merge"] == [kept.merge_id]
    assert [s.position for s in drawn] == list(range(len(drawn)))

    edge = next(s for s in drawn if s.kind == "edge")
    assert (edge.produced_by, edge.model, edge.quality_tier) == ("agent-small", "model-a", 1)
    assert edge.shown["relation"] == "funds"
    assert edge.shown["from"]["name"].startswith("from ")
    assert [p["text"] for p in edge.shown["passages"]] == [
        "The agency funds the programme.",
        "A second passage.",
    ]
    assert edge.shown["chunk_ids"] == chunk_ids

    merged = next(s for s in drawn if s.kind == "merge")
    assert merged.produced_by == "user"
    assert merged.shown["source"]["entity_id"] == kept.source_entity_id
    assert merged.shown["target"]["entity_id"] == kept.target_entity_id
    assert merged.grade is None


async def test_the_same_seed_draws_the_same_sample(sess) -> None:
    edges = [await an_edge(sess, at=INSIDE) for _ in range(40)]
    ids = {e.edge_id for e in edges}

    first = await graphaudit.sample_ids(sess, "edge", seed="s1", start=START, end=NOW, n=10)
    again = await graphaudit.sample_ids(sess, "edge", seed="s1", start=START, end=NOW, n=10)
    other = await graphaudit.sample_ids(sess, "edge", seed="s2", start=START, end=NOW, n=10)
    assert first == again
    assert len(first) == 10 and set(first) <= ids
    # Not the oldest ten: the order is the seed's, not the table's.
    assert first != sorted(ids)[:10]
    assert other != first

    row = await graphaudit.draw(sess, now=NOW, since=START, seed="s1", edges=10, merges=0)
    recorded = [s.item_id for s in await samples_of(sess, row.draw_id)]
    assert recorded == first
    assert row.seed == "s1"


async def test_a_fresh_seed_is_chosen_and_recorded_when_none_is_given(sess) -> None:
    for _ in range(3):
        await an_edge(sess, at=INSIDE)
    row = await graphaudit.draw(sess, now=NOW, since=START)
    assert row.seed and len(row.seed) == 16
    recorded = [s.item_id for s in await samples_of(sess, row.draw_id)]
    assert recorded == await graphaudit.sample_ids(
        sess, "edge", seed=row.seed, start=START, end=NOW, n=graphaudit.EDGES_PER_DRAW
    )


async def test_the_sample_is_capped_by_the_size_asked_for(sess) -> None:
    for _ in range(12):
        await an_edge(sess, at=INSIDE)
    row = await graphaudit.draw(sess, now=NOW, since=START, edges=5, merges=0, seed="cap")
    assert row.edge_population == 12
    assert len(await samples_of(sess, row.draw_id)) == 5


async def test_a_window_with_nothing_in_it_records_nothing(sess) -> None:
    empty_start = dt.datetime(1999, 1, 1, tzinfo=UTC)
    empty_end = dt.datetime(1999, 2, 1, tzinfo=UTC)
    assert await graphaudit.draw(sess, now=empty_end, since=empty_start) is None
    assert (
        await sess.scalar(select(GraphAuditDraw).where(GraphAuditDraw.window_start == empty_start))
        is None
    )


async def test_each_draw_starts_where_the_last_ended(sess) -> None:
    first_end = dt.datetime(2099, 1, 10, tzinfo=UTC)
    second_end = dt.datetime(2099, 2, 10, tzinfo=UTC)
    await an_edge(sess, at=dt.datetime(2099, 1, 5, tzinfo=UTC))
    later = await an_edge(sess, at=dt.datetime(2099, 1, 20, tzinfo=UTC))

    first = await graphaudit.draw(sess, now=first_end, since=dt.datetime(2099, 1, 1, tzinfo=UTC))
    second = await graphaudit.draw(sess, now=second_end)
    assert second.window_start == first.window_end == first_end
    assert [s.item_id for s in await samples_of(sess, second.draw_id)] == [later.edge_id]

    # Minutes after a draw is too soon: it would split one month into two thin samples.
    with pytest.raises(graphaudit.AuditRefused, match="Too soon"):
        await graphaudit.draw(sess, now=second_end + dt.timedelta(minutes=5))


@pytest.mark.parametrize(
    ("kwargs", "words"),
    [
        ({"edges": -1}, "0 to"),
        ({"merges": graphaudit.MAX_PER_DRAW + 1}, "0 to"),
        ({"edges": 0, "merges": 0}, "nothing"),
        ({"now": dt.datetime(2001, 4, 1)}, "zone"),
        ({"since": NOW - dt.timedelta(hours=2)}, "Too soon"),
    ],
)
async def test_a_draw_that_cannot_be_made_is_refused_and_records_nothing(
    sess, kwargs, words
) -> None:
    await an_edge(sess, at=INSIDE)
    before = await sess.scalar(select(text("count(*)")).select_from(GraphAuditDraw))
    call = {"now": NOW, "since": START, **kwargs}
    with pytest.raises(graphaudit.AuditRefused, match=words):
        await graphaudit.draw(sess, **call)
    assert await sess.scalar(select(text("count(*)")).select_from(GraphAuditDraw)) == before


async def test_a_grade_changes_the_sample_and_never_the_graph(sess) -> None:
    edge = await an_edge(sess, at=INSIDE, confidence=0.7)
    entry = await a_merge(sess, at=INSIDE)
    row = await graphaudit.draw(sess, now=NOW, since=START, seed="g")
    drawn = {s.kind: s for s in await samples_of(sess, row.draw_id)}

    graded = await graphaudit.grade(
        sess, drawn["edge"].sample_id, "wrong", graded_by="user", note="  not what it says  "
    )
    assert graded.grade == "wrong" and graded.note == "not what it says"
    assert graded.graded_by == "user" and graded.graded_at is not None
    await graphaudit.grade(sess, drawn["merge"].sample_id, "wrong", graded_by="user")

    # A wrong relation stays; a wrong merge stays merged. A person decides what to do.
    await sess.refresh(edge)
    assert edge.confidence == 0.7 and edge.supporting_chunk_ids == [1]
    await sess.refresh(entry)
    assert entry.reversed_at is None
    source = await sess.get(Entity, entry.source_entity_id)
    await sess.refresh(source)
    assert source.redirects_to == entry.target_entity_id

    # A second grade replaces the first, note included.
    again = await graphaudit.grade(sess, drawn["edge"].sample_id, "correct", graded_by="user")
    assert again.grade == "correct" and again.note is None


async def test_a_grade_that_cannot_be_given_is_refused(sess) -> None:
    await an_edge(sess, at=INSIDE)
    row = await graphaudit.draw(sess, now=NOW, since=START, seed="r")
    sample = (await samples_of(sess, row.draw_id))[0]
    with pytest.raises(graphaudit.AuditRefused, match="not a grade"):
        await graphaudit.grade(sess, sample.sample_id, "maybe", graded_by="user")
    with pytest.raises(graphaudit.AuditRefused, match="who gave it"):
        await graphaudit.grade(sess, sample.sample_id, "correct", graded_by="")
    with pytest.raises(graphaudit.AuditRefused, match="at most"):
        await graphaudit.grade(sess, sample.sample_id, "correct", graded_by="u", note="x" * 2001)
    with pytest.raises(graphaudit.AuditRefused, match="No sampled item"):
        await graphaudit.grade(sess, 10**12, "correct", graded_by="user")
    await sess.refresh(sample)
    assert sample.grade is None


async def test_precision_leaves_unsure_out_and_splits_by_producer(sess) -> None:
    for _ in range(4):
        await an_edge(sess, at=INSIDE, produced_by="agent-small", model="model-a")
    for _ in range(2):
        await an_edge(sess, at=INSIDE, produced_by="agent-large", model="model-b", quality_tier=2)
    row = await graphaudit.draw(sess, now=NOW, since=START, seed="p", merges=0)
    drawn = await samples_of(sess, row.draw_id)
    small = [s for s in drawn if s.model == "model-a"]
    large = [s for s in drawn if s.model == "model-b"]
    for s, verdict in zip(small, ("correct", "correct", "wrong", "unsure"), strict=True):
        await graphaudit.grade(sess, s.sample_id, verdict, graded_by="user")
    await graphaudit.grade(sess, large[0].sample_id, "correct", graded_by="user")

    detail = await graphaudit.draw_detail(sess, row.draw_id)
    edges = detail.draw.edges
    assert (edges.drawn, edges.graded, edges.correct, edges.wrong, edges.unsure) == (6, 5, 3, 1, 1)
    assert edges.precision == pytest.approx(3 / 4)
    assert edges.low < 0.75 < edges.high
    assert detail.draw.merges.precision is None and detail.draw.merges.drawn == 0

    by_model = {p.model: p.tally for p in detail.draw.by_producer}
    assert by_model["model-a"].precision == pytest.approx(2 / 3)
    assert by_model["model-b"].precision == 1.0 and by_model["model-b"].graded == 1
    assert [s.position for s in detail.samples] == sorted(s.position for s in detail.samples)

    page = await graphaudit.report(sess, limit=120)
    ours = next(d for d in page.draws if d.draw_id == row.draw_id)
    assert ours.edges == edges
    assert page.ungraded >= 1  # model-b's second edge
    assert [d.window_end for d in page.draws] == sorted(
        (d.window_end for d in page.draws), reverse=True
    )

    with pytest.raises(graphaudit.AuditRefused, match="No draw"):
        await graphaudit.draw_detail(sess, 10**12)


async def _raw(sess, sql: str, **params) -> None:
    async with sess.begin_nested():
        await sess.execute(text(sql), params)


async def test_the_database_refuses_half_a_grade_an_unknown_grade_and_a_second_draw(
    sess,
) -> None:
    """Raw SQL, so the CHECKs are what is tested and not SQLAlchemy's own validation."""
    await an_edge(sess, at=INSIDE)
    row = await graphaudit.draw(sess, now=NOW, since=START, seed="db")
    sample = (await samples_of(sess, row.draw_id))[0]
    with pytest.raises(IntegrityError, match="audit_graded_together"):
        await _raw(
            sess,
            "UPDATE graph_audit_samples SET grade = 'wrong' WHERE sample_id = :id",
            id=sample.sample_id,
        )
    with pytest.raises(IntegrityError, match="audit_grade"):
        await _raw(
            sess,
            "UPDATE graph_audit_samples SET grade = 'maybe', graded_at = now(), "
            "graded_by = 'u' WHERE sample_id = :id",
            id=sample.sample_id,
        )
    with pytest.raises(IntegrityError, match="audit_kind"):
        await _raw(
            sess,
            "INSERT INTO graph_audit_samples (draw_id, kind, item_id, position, shown) "
            "VALUES (:d, 'node', 1, 99, '{}')",
            d=row.draw_id,
        )
    # Two schedulers racing over one period: the second draw of the same start is refused.
    with pytest.raises(IntegrityError, match="window_start"):
        await _raw(
            sess,
            "INSERT INTO graph_audit_draws (seed, window_start, window_end, edge_population, "
            "merge_population, merges_reversed, drawn_by) VALUES ('x', :s, :e, 0, 0, 0, 't')",
            s=START,
            e=NOW,
        )


async def test_the_scheduled_draw_says_so_in_the_bell(sess, monkeypatch) -> None:
    from worker import graphaudit as job

    real_commit = sess.commit

    @contextlib.asynccontextmanager
    async def through_the_test_session():
        sess.commit = sess.flush
        try:
            yield sess
        finally:
            sess.commit = real_commit

    async def nothing():
        return None

    sent: list[str] = []

    class Bot:
        async def send(self, message):
            sent.append(message)
            return True

        async def aclose(self):
            return None

    monkeypatch.setattr(job, "session", through_the_test_session)
    monkeypatch.setattr(job, "dispose_engines", nothing)
    monkeypatch.setattr(job.Telegram, "from_env", classmethod(lambda cls: Bot()))

    last_end = dt.datetime(2098, 1, 1, tzinfo=UTC)
    sess.add(
        GraphAuditDraw(
            seed="earlier",
            window_start=last_end - dt.timedelta(days=30),
            window_end=last_end,
            edge_population=0,
            merge_population=0,
            merges_reversed=0,
            drawn_by="test",
        )
    )
    await an_edge(sess, at=last_end + dt.timedelta(days=3))
    now = last_end + dt.timedelta(days=30)

    draw_id = await job.run_draw(now=now)
    assert draw_id is not None
    note = await sess.scalar(
        select(Notification)
        .where(Notification.notification_type == "job_complete")
        .order_by(Notification.notification_id.desc())
    )
    assert note.payload == {"job": job.JOB, "draw_id": draw_id}
    assert note.surface == "admin"
    assert note.title.startswith("Graph audit: 1 relations")
    assert sent and sent[0] == note.body

    # Run again straight away: too soon, so nothing is drawn and the job does not fail.
    assert await job.run_draw(now=now + dt.timedelta(minutes=1)) is None
    assert len(sent) == 1


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "true")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


async def test_the_routes_refuse_in_words(client) -> None:
    listed = await client.get("/api/admin/graph-audit")
    assert listed.status_code == 200 and set(listed.json()) == {"draws", "ungraded"}
    missing = await client.get("/api/admin/graph-audit/999999999999")
    assert missing.status_code == 404 and "No draw" in missing.json()["detail"]
    unknown = await client.post(
        "/api/admin/graph-audit/samples/999999999999", json={"grade": "correct"}
    )
    assert unknown.status_code == 409 and "No sampled item" in unknown.json()["detail"]
    bogus = await client.post("/api/admin/graph-audit/samples/1", json={"grade": "maybe"})
    assert bogus.status_code == 422
    extra = await client.post(
        "/api/admin/graph-audit/samples/1", json={"grade": "wrong", "delete": True}
    )
    assert extra.status_code == 422
    assert (await client.get("/api/explore/graph-audit")).status_code == 404
