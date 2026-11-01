"""What is new, by topic and kind (task B-237), against a real Postgres.

Rolled back. Every row this file makes is dated in 2001, a year the dev corpus holds nothing
from, and filed under topics unique to the run, so the dev data never leaks into a count. The
route is a thin wrapper, checked for its permission by `tests/unit/test_route_permissions.py`
and for its shape by the web's field-list drift test; one test calls it for the refusals.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select, update

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import (
    AttributeDefinition,
    AttributeValue,
    Chunk,
    ChunkTopics,
    Edge,
    Entity,
    Source,
    TopicConfig,
)
from meridian_core.sources import upsert_source
from meridian_core.stats import corpus_stats
from meridian_core.whatsnew import MAX_WINDOW, one_line, pick_excerpt, whats_new

pytestmark = pytest.mark.usefixtures("require_db")

UTC = dt.UTC
SINCE = dt.datetime(2001, 1, 10, 12, tzinfo=UTC)
AS_OF = dt.datetime(2001, 1, 20, 12, tzinfo=UTC)
INSIDE = dt.datetime(2001, 1, 15, 12, tzinfo=UTC)
BEFORE = dt.datetime(2001, 1, 5, 12, tzinfo=UTC)
AFTER = dt.datetime(2001, 1, 25, 12, tzinfo=UTC)

PROSE = (
    "Kerbside loading bays in the made-up town of Northfield were reallocated over a year, "
    "and delivery dwell time fell while cycle-lane conflicts were logged at every site."
)
REFERENCES = "\n".join(
    f"Smith, J. ({1990 + i}). A made-up study of kerbs. Journal of Streets, {i}(2), 1-9."
    for i in range(8)
)


@pytest.fixture
async def sess(session_for):
    return await session_for("rw")


class Corpus:
    """Topics, sources, passages and findings of this test's own."""

    def __init__(self, sess) -> None:
        self.sess = sess
        self.marker = uuid.uuid4().hex[:8]
        self.n = 0

    async def topic(self, name: str, weight: float, **kw) -> str:
        topic = f"{name}-{self.marker}"
        self.sess.add(TopicConfig(topic=topic, weight=weight, **kw))
        await self.sess.flush()
        return topic

    async def source(
        self,
        labels: list[str] | None,
        *,
        created: dt.datetime = INSIDE,
        passages: list[str] | None = None,
        scores: list[dict[str, float] | None] | None = None,
        **values,
    ) -> Source:
        self.n += 1
        source, _ = await upsert_source(
            self.sess,
            f"https://news.northfield-{self.marker}.test/{self.n}",
            checksum=f"sha256:{uuid.uuid4().hex}",
            source_tier="press",
            title=f"Northfield item {self.n}",
        )
        if passages:
            await replace_chunks(
                self.sess,
                source.source_id,
                [ChunkWrite(text=t, chunk_index=i) for i, t in enumerate(passages)],
            )
        await self.sess.execute(
            update(Source)
            .where(Source.source_id == source.source_id)
            .values(topic_labels=labels, created_at=created, **values)
        )
        await self.sess.flush()
        ids = list(
            await self.sess.scalars(
                select(Chunk.chunk_id)
                .where(Chunk.source_id == source.source_id)
                .order_by(Chunk.chunk_index)
            )
        )
        for chunk_id, score in zip(ids, scores or [], strict=False):
            if score is not None:
                self.sess.add(
                    ChunkTopics(
                        chunk_id=chunk_id,
                        topic_labels=list(score),
                        topic_scores=score,
                        topic_basis="t",
                    )
                )
        await self.sess.flush()
        await self.sess.refresh(source)
        source.chunk_ids = ids  # type: ignore[attr-defined]
        return source

    async def node(self, name: str, **kw) -> Entity:
        entity = Entity(canonical_name=f"{name} {self.marker}", node_type="concept", **kw)
        self.sess.add(entity)
        await self.sess.flush()
        return entity


async def test_new_sources_are_grouped_by_leading_topic_and_counted_by_kind(sess) -> None:
    """In the window, kept, once each under its first shown topic; junk and copies out."""
    c = Corpus(sess)
    kerb = await c.topic("kerb", 0.9, description="Made-up kerbside studies.")
    heat = await c.topic("heat", 0.5)
    gone = await c.topic("gone", 0.7, status="archived")

    news = await c.source([kerb], doc_kind="news", created=INSIDE)
    later = await c.source([kerb, heat], doc_kind="report", created=INSIDE + dt.timedelta(hours=2))
    # Led by an archived topic, so it falls to its next shown one.
    via_next = await c.source([gone, heat], doc_kind="press_release")
    await c.source([kerb], doc_kind="listing")  # counted, never listed
    await c.source([kerb], doc_kind="news", created=BEFORE)  # before the window
    await c.source([kerb], doc_kind="news", created=AFTER)  # after "as of"
    await c.source([kerb], doc_kind="news", retention_tier="junk")
    await c.source([kerb], doc_kind="news", duplicate_of=news.source_id, duplicate_reason="exact")
    await c.source([], doc_kind="opinion")  # examined, on no topic
    await c.source(None, doc_kind="paper")  # not examined yet

    found = await whats_new(sess, since=SINCE, as_of=AS_OF, topics=[kerb, heat, gone])
    groups = {g.topic: g for g in found.topics}

    assert [g.topic for g in found.topics] == [kerb, heat]  # heaviest first; archived out
    assert groups[kerb].description == "Made-up kerbside studies."
    assert groups[kerb].total == 3
    assert {(k.doc_kind, k.sources) for k in groups[kerb].by_kind} == {
        ("news", 1),
        ("report", 1),
        ("listing", 1),
    }
    # Newest first, and a listing is in the count but not the list.
    assert [s.source_id for s in groups[kerb].sources] == [later.source_id, news.source_id]
    assert groups[kerb].sources[0].other_topics == [heat]
    assert [s.source_id for s in groups[heat].sources] == [via_next.source_id]
    assert groups[heat].sources[0].other_topics == []  # the archived topic is not named

    first = groups[kerb].sources[0]
    assert first.site == f"news.northfield-{c.marker}.test"
    assert first.doc_kind == "report"
    assert first.added == INSIDE + dt.timedelta(hours=2)


async def test_the_unfiltered_total_is_the_landings_delta(sess) -> None:
    """One counting rule: sources new since an instant, summed over topics and elsewhere,
    equal the landing's `new_sources` for the same instant (`B-240`)."""
    c = Corpus(sess)
    kerb = await c.topic("kerb", 0.9)
    await c.source([kerb], doc_kind="news")
    await c.source([], doc_kind="news")
    await c.source([kerb], retention_tier="junk")
    since = dt.datetime.now(UTC) - dt.timedelta(minutes=5)
    for row in await sess.scalars(select(Source).where(Source.url.contains(c.marker))):
        row.created_at = dt.datetime.now(UTC) - dt.timedelta(minutes=1)
    await sess.flush()

    found = await whats_new(sess, since=since)
    stats = await corpus_stats(sess, since=since)

    assert sum(g.total for g in found.topics) + found.elsewhere == stats.new_sources


async def test_elsewhere_counts_what_no_shown_topic_leads(sess) -> None:
    c = Corpus(sess)
    kerb = await c.topic("kerb", 0.9)
    heat = await c.topic("heat", 0.5)
    await c.source([kerb])
    await c.source([heat])
    await c.source([])
    await c.source(None)

    found = await whats_new(sess, since=SINCE, as_of=AS_OF, topics=[kerb])

    assert [g.topic for g in found.topics] == [kerb]
    # Nothing else in the dev corpus is dated 2001: the heat source, the off-topic one and
    # the unexamined one.
    assert found.elsewhere == 3


async def test_a_kind_filter_narrows_counts_and_list(sess) -> None:
    c = Corpus(sess)
    kerb = await c.topic("kerb", 0.9)
    paper = await c.source([kerb], doc_kind="paper")
    await c.source([kerb], doc_kind="news")

    found = await whats_new(sess, since=SINCE, as_of=AS_OF, topics=[kerb], doc_kinds=["paper"])

    group = found.topics[0]
    assert group.total == 1
    assert [s.source_id for s in group.sources] == [paper.source_id]


async def test_by_publication_date_ignores_when_it_was_added_and_future_dates(sess) -> None:
    c = Corpus(sess)
    kerb = await c.topic("kerb", 0.9)
    dated = await c.source([kerb], publication_date=dt.date(2001, 1, 12), created=BEFORE)
    await c.source([kerb], publication_date=dt.date(2000, 6, 1))  # added now, old
    await c.source([kerb], publication_date=dt.date(2001, 3, 1), created=BEFORE)  # past as_of
    await c.source([kerb])  # undated

    found = await whats_new(sess, since=SINCE, as_of=AS_OF, basis="published", topics=[kerb])

    assert found.basis == "published"
    assert found.topics[0].total == 1
    assert [s.source_id for s in found.topics[0].sources] == [dated.source_id]


async def test_the_excerpt_is_the_passage_scored_highest_for_its_topic(sess) -> None:
    """Mechanical: the labeller's passage score for the group's topic, then reading order;
    a reference list and a heading are passed over."""
    c = Corpus(sess)
    kerb = await c.topic("kerb", 0.9)
    heat = await c.topic("heat", 0.5)
    source = await c.source(
        [kerb, heat],
        passages=["Contents", REFERENCES, "A short heading", PROSE, "Heat " + PROSE],
        scores=[
            {kerb: 0.9},
            {kerb: 0.8},
            {kerb: 0.7},
            {kerb: 0.6, heat: 0.1},
            {kerb: 0.2, heat: 0.95},
        ],
    )
    unscored = await c.source([kerb], passages=["Title", PROSE, "Later " + PROSE])

    found = await whats_new(sess, since=SINCE, as_of=AS_OF, topics=[kerb, heat])
    listed = {s.source_id: s for g in found.topics for s in g.sources}

    excerpt = listed[source.source_id].excerpt
    assert excerpt is not None
    # Scored highest for kerb (its leading topic) is "Contents", a heading; the reference list
    # is refused; so the prose passage scored 0.6, not the heat one scored higher for heat.
    assert excerpt.chunk_id == source.chunk_ids[3]
    assert excerpt.text == one_line(PROSE)
    # With no scores, reading order, past the heading.
    assert listed[unscored.source_id].excerpt.chunk_id == unscored.chunk_ids[1]


async def test_a_source_without_a_readable_passage_has_no_excerpt_and_model_paths_see_cleared(
    sess,
) -> None:
    """§2.5: for a model, only cleared sources, excerpts and citations."""
    c = Corpus(sess)
    kerb = await c.topic("kerb", 0.9)
    cleared = await c.source([kerb], passages=[PROSE], trust_state="cleared")
    unscreened = await c.source([kerb], passages=[PROSE], trust_state="unscreened")
    bare = await c.source([kerb], trust_state="cleared")

    reader = await whats_new(sess, since=SINCE, as_of=AS_OF, topics=[kerb])
    model = await whats_new(sess, since=SINCE, as_of=AS_OF, topics=[kerb], cleared_only=True)

    shown = {s.source_id: s for s in reader.topics[0].sources}
    assert set(shown) == {cleared.source_id, unscreened.source_id, bare.source_id}
    assert shown[bare.source_id].excerpt is None
    assert shown[unscreened.source_id].excerpt is not None  # Find shows it too
    assert {s.source_id for s in model.topics[0].sources} == {cleared.source_id, bare.source_id}


async def test_new_findings_carry_their_first_readable_citation(sess) -> None:
    c = Corpus(sess)
    kerb = await c.topic("kerb", 0.9)
    heat = await c.topic("heat", 0.5)
    good = await c.source([kerb], passages=[PROSE], trust_state="cleared")
    hidden = await c.source([kerb], passages=[PROSE], trust_state="quarantined")
    junk = await c.source([kerb], passages=[PROSE], retention_tier="junk")
    bays = await c.node("Loading bays", topic_labels=[kerb])
    dwell = await c.node("Dwell time")
    merged = await c.node("Old name")
    merged.redirects_to = dwell.entity_id

    def edge(a, b, chunks, created=INSIDE, **kw) -> Edge:
        e = Edge(
            from_node=a.entity_id,
            to_node=b.entity_id,
            relation_type="reduces",
            supporting_chunk_ids=chunks,
            created_at=created,
            **{"topic_labels": [kerb], **kw},
        )
        sess.add(e)
        return e

    # The junk passage is cited first; the citation shown is the first a reader may open.
    shown = edge(bays, dwell, [junk.chunk_ids[0], good.chunk_ids[0]])
    edge(bays, dwell, [good.chunk_ids[0]], created=BEFORE)  # before the window
    edge(bays, dwell, [junk.chunk_ids[0]])  # no passage a reader may open
    # Quarantined text a reader may open (Find shows it, marked), but a model may not read.
    screened = edge(bays, dwell, [hidden.chunk_ids[0]])
    edge(bays, merged, [good.chunk_ids[0]])  # an end merged away
    edge(dwell, dwell, [good.chunk_ids[0]], topic_labels=[heat])
    definition = AttributeDefinition(name=f"bay-count-{c.marker}", scope="global")
    sess.add(definition)
    await sess.flush()
    tag = AttributeValue(
        entity_id=bays.entity_id,
        attribute_id=definition.attribute_id,
        value="12",
        supporting_chunk_ids=[good.chunk_ids[0]],
        created_at=INSIDE,
    )
    sess.add(tag)
    await sess.flush()

    found = await whats_new(sess, since=SINCE, as_of=AS_OF, topics=[kerb])

    by_id = {(f.kind, f.finding_id): f for f in found.findings}
    # Heat's is out: topics were asked for, and it is on none of them.
    assert set(by_id) == {
        ("link", shown.edge_id),
        ("link", screened.edge_id),
        ("tag", tag.value_id),
    }
    assert found.findings_total == 3
    link = by_id[("link", shown.edge_id)]
    assert (link.subject, link.relation, link.object) == (
        bays.canonical_name,
        "reduces",
        dwell.canonical_name,
    )
    assert link.topic == kerb
    assert link.citation.chunk_id == good.chunk_ids[0]
    assert link.citation.source_id == good.source_id
    t = by_id[("tag", tag.value_id)]
    assert (t.relation, t.object, t.object_id) == (definition.name, "12", None)

    # For a model, the quarantined edge stays out and the citations are cleared ones.
    model = await whats_new(sess, since=SINCE, as_of=AS_OF, topics=[kerb], cleared_only=True)
    assert {f.citation.source_id for f in model.findings} == {good.source_id}
    assert screened.edge_id not in {f.finding_id for f in model.findings if f.kind == "link"}
    assert model.findings_total == 2


async def test_the_window_is_bounded_and_refuses_what_it_cannot_answer(sess) -> None:
    with pytest.raises(ValueError, match="offset"):
        await whats_new(sess, since=dt.datetime(2001, 1, 1), as_of=AS_OF)
    with pytest.raises(ValueError, match="after"):
        await whats_new(sess, since=AFTER, as_of=AS_OF)
    found = await whats_new(sess, since=dt.datetime(1990, 1, 1, tzinfo=UTC), as_of=AS_OF)
    assert found.since == AS_OF - MAX_WINDOW  # narrowed, and said


def test_pick_excerpt_passes_over_references_and_headings() -> None:
    assert pick_excerpt([(1, REFERENCES, None), (2, "Heading", 3), (3, PROSE, 4)]).chunk_id == 3
    # All short: the first that is not a reference list.
    assert pick_excerpt([(1, REFERENCES, None), (2, "Heading", 3)]).chunk_id == 2
    assert pick_excerpt([(1, REFERENCES, None)]) is None
    assert pick_excerpt([]) is None


def test_one_line_cuts_at_a_word() -> None:
    text = "word " * 100
    cut = one_line(text, 50)
    assert len(cut) <= 50 and cut.endswith("…") and "  " not in cut
    assert one_line("a\n\nb") == "a b"


@pytest.fixture
async def client():
    import httpx

    from api.main import create_app
    from meridian_core.db import dispose_engines

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as c:
        yield c
    await dispose_engines()


async def test_the_route_answers_on_the_read_role_and_refuses_bad_windows(client) -> None:
    """The default window is a week; a naive instant, a kind the database does not know and
    an oversized page are refused with the argument named."""
    ok = await client.get("/api/explore/new", params={"per_topic": 0, "findings": 0})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    since, as_of = (dt.datetime.fromisoformat(body[k]) for k in ("since", "as_of"))
    assert dt.timedelta(days=6, hours=23) < as_of - since <= dt.timedelta(days=7)
    assert all(g["sources"] == [] for g in body["topics"])

    naive = await client.get("/api/explore/new", params={"since": "2026-10-01T00:00:00"})
    assert naive.status_code == 422 and "offset" in naive.text
    kind = await client.get("/api/explore/new", params={"doc_kind": "rumour"})
    assert kind.status_code == 422 and "doc_kind" in kind.text
    big = await client.get("/api/explore/new", params={"per_topic": 999})
    assert big.status_code == 422 and "per_topic" in big.text
    # A `since` in the future answers "nothing yet", not an error: clocks disagree.
    future = await client.get("/api/explore/new", params={"since": "2999-01-01T00:00:00Z"})
    assert future.status_code == 200
    assert future.json()["since"] == future.json()["as_of"]
