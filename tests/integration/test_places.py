"""Which places a source is about, against Postgres (task P2-23, spec §7.2).

The pure rules are `tests/unit/test_places.py`. This file is what only the
database can answer: which sources the pass picks up, what it writes, NULL
against `{}`, when a tag goes stale (a re-crawl, a new place edge), that an
ambiguous gazetteer term stays out of the count, and that the filters and
Gaps read the column the pass wrote.

Most tests run in one transaction the fixture discards — the pass's commits
become flushes — and start their scan past the dev corpus, so nothing here
tags the real crawl. The API tests must commit, because the app reads on its
own connection; they delete what they made.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import delete, func, select, update

from api.main import create_app
from meridian_core import gaps
from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.db import dispose_engines
from meridian_core.models import Chunk, Edge, Entity, GazetteerTerm, Source, TopicConfig
from meridian_core.placenames import CITIES, COUNTRIES
from meridian_core.places import (
    DOMAIN,
    ENTITY,
    GAZETTEER,
    TEXT,
    basis_fingerprint,
    comparison_set,
    load_vocabulary,
    sources_awaiting,
)
from meridian_core.search import SearchFilters, search
from meridian_core.sources import upsert_source
from worker.places import PlaceTagger

pytestmark = pytest.mark.usefixtures("require_db")

JAPAN = COUNTRIES["JP"][0]
SINGAPORE = COUNTRIES["SG"][0]
SEOUL = CITIES["KRSEL"][0]


def often(name: str, times: int = 6) -> str:
    return " ".join(f"{name} is discussed here." for _ in range(times))


@pytest.fixture
async def world(session_for):
    """The test's transaction and a tagger that starts past the dev corpus."""
    sess = await session_for("rw")
    start_after = int(await sess.scalar(select(func.coalesce(func.max(Source.source_id), 0))))
    scope = f"zz-{uuid.uuid4().hex[:8]}"

    @asynccontextmanager
    async def factory():
        # Commits become flushes so the whole test is one transaction the
        # fixture can discard.
        sess.commit = sess.flush
        yield sess

    def tagger() -> PlaceTagger:
        return PlaceTagger(session_factory=factory, start_after=start_after)

    yield sess, tagger, scope
    await sess.rollback()


async def a_source(
    sess,
    text: str | None,
    *,
    scope: str,
    host: str | None = None,
    tier: str = "institutional",
    title: str | None = None,
) -> int:
    host = host or f"t{uuid.uuid4().hex[:12]}.test"
    source, _ = await upsert_source(
        sess,
        f"https://{host}/{uuid.uuid4().hex[:6]}",
        checksum=f"sha256:{uuid.uuid4().hex}",
        source_tier=tier,
        language=scope,
        title=title,
    )
    if text is not None:
        await replace_chunks(sess, source.source_id, [ChunkWrite(text=text, chunk_index=0)])
    await sess.flush()
    return source.source_id


async def row(sess, source_id: int) -> Source:
    sess.expire_all()
    return await sess.get(Source, source_id)


# ---------------------------------------------------------------------------
# What a pass writes
# ---------------------------------------------------------------------------


async def test_a_report_writes_nothing(world) -> None:
    sess, tagger, scope = world
    sid = await a_source(sess, often(JAPAN), scope=scope)

    stats = await tagger().run(apply=False)

    assert stats.examined >= 1
    assert stats.per_place["JP"] >= 1
    source = await row(sess, sid)
    assert source.places is None and source.places_examined_at is None


async def test_apply_writes_the_places_the_evidence_and_the_basis(world) -> None:
    sess, tagger, scope = world
    sid = await a_source(sess, often(JAPAN), scope=scope)

    await tagger().run(apply=True)

    source = await row(sess, sid)
    assert source.places == ["JP"]
    assert source.place_evidence["names"] == {"JP": 6}
    assert source.place_evidence["decided"] == {"JP": [TEXT]}
    assert source.places_examined_at is not None
    assert source.place_basis == basis_fingerprint(await load_vocabulary(sess))


async def test_a_single_passing_mention_is_examined_and_tags_nothing(world) -> None:
    """Rejection, through the whole pass: `{}`, not NULL — examined, and about
    no place — and not the country named once."""
    sess, tagger, scope = world
    sid = await a_source(
        sess, f"A report on buses. A delegation from {JAPAN} visited once.", scope=scope
    )

    await tagger().run(apply=True)

    source = await row(sess, sid)
    assert source.places == []
    assert source.place_evidence["names"] == {"JP": 1}


async def test_a_source_with_no_live_text_stays_unexamined(world) -> None:
    """NULL is the truth for a source nothing can read: calling it "about no
    place" would be a claim about text nobody has."""
    sess, tagger, scope = world
    sid = await a_source(sess, None, scope=scope)

    await tagger().run(apply=True)

    assert (await row(sess, sid)).places is None


async def test_a_foreign_government_publisher_writing_about_another_country(world) -> None:
    """Rejection: a national-government domain does not tag its own country on
    a page about another one that names its own jurisdiction once."""
    sess, tagger, scope = world
    sid = await a_source(
        sess,
        often(JAPAN, 8) + f" Published in {SINGAPORE}.",
        scope=scope,
        host="www.agency.gov.sg",
        tier="government",
    )

    await tagger().run(apply=True)

    source = await row(sess, sid)
    assert source.places == ["JP"]
    assert source.place_evidence["domain"] == {"country": "SG", "strength": "government"}


async def test_a_government_page_naming_no_place_is_about_its_jurisdiction(world) -> None:
    sess, tagger, scope = world
    sid = await a_source(
        sess, "Fares change next month.", scope=scope, host="www.agency.gov.sg", tier="government"
    )

    await tagger().run(apply=True)

    source = await row(sess, sid)
    assert source.places == ["SG"]
    assert source.place_evidence["decided"] == {"SG": [DOMAIN]}


# ---------------------------------------------------------------------------
# The gazetteer (§5.6)
# ---------------------------------------------------------------------------


async def a_term(sess, *, ambiguous: bool, jurisdiction: str = "KR") -> str:
    canonical = f"Qx Transit Office {uuid.uuid4().hex[:8]}"
    sess.add(
        GazetteerTerm(
            canonical=canonical,
            aliases=[],
            entity_type="agency",
            jurisdiction=jurisdiction,
            ambiguous=ambiguous,
            approved=True,
            source="manual",
        )
    )
    await sess.flush()
    return canonical


async def test_an_ambiguous_gazetteer_term_does_not_tag(world) -> None:
    """Rejection: §5.6 marks a term ambiguous when its surface collides, and
    then the table only offers candidates. Counting it would decide what the
    resolver deliberately leaves undecided."""
    sess, tagger, scope = world
    term = await a_term(sess, ambiguous=True)
    sid = await a_source(sess, often(term, 8), scope=scope)

    await tagger().run(apply=True)

    source = await row(sess, sid)
    assert source.places == []
    assert source.place_evidence["gazetteer"] == {}


async def test_an_unambiguous_jurisdictional_term_tags_its_country(world) -> None:
    sess, tagger, scope = world
    term = await a_term(sess, ambiguous=False)
    sid = await a_source(sess, often(term, 5), scope=scope)

    await tagger().run(apply=True)

    source = await row(sess, sid)
    assert source.places == ["KR"]
    assert source.place_evidence["decided"] == {"KR": [GAZETTEER]}


async def test_a_new_gazetteer_term_changes_the_basis_and_re_examines(world) -> None:
    sess, tagger, scope = world
    await a_source(sess, often(JAPAN), scope=scope)
    await tagger().run(apply=True)
    before = basis_fingerprint(await load_vocabulary(sess))

    await a_term(sess, ambiguous=False)
    after = basis_fingerprint(await load_vocabulary(sess))

    assert after != before
    stats = await tagger().run(apply=False)
    assert stats.examined >= 1


# ---------------------------------------------------------------------------
# Staleness — the queue
# ---------------------------------------------------------------------------


async def test_a_second_pass_finds_nothing_to_do(world) -> None:
    sess, tagger, scope = world
    await a_source(sess, often(JAPAN), scope=scope)
    await tagger().run(apply=True)

    assert (await tagger().run(apply=True)).examined == 0


async def test_a_rewritten_source_is_re_examined(world) -> None:
    sess, tagger, scope = world
    sid = await a_source(sess, often(JAPAN), scope=scope)
    await tagger().run(apply=True)
    assert (await row(sess, sid)).places == ["JP"]

    # A re-crawl: new live chunks, written after the examination.
    await replace_chunks(sess, sid, [ChunkWrite(text=often(SINGAPORE), chunk_index=0)])
    await sess.execute(
        update(Chunk)
        .where(Chunk.source_id == sid, Chunk.superseded_at.is_(None))
        .values(created_at=func.clock_timestamp() + dt.timedelta(hours=1))
    )

    stats = await tagger().run(apply=True)

    assert stats.examined == 1
    assert (await row(sess, sid)).places == ["SG"]


async def test_a_new_place_edge_citing_the_source_re_examines_it(world) -> None:
    """The entity signal: a model's claim about a place, drawn from this
    source's chunk, tags the source — and arriving after the examination puts
    the source back in the queue."""
    sess, tagger, scope = world
    sid = await a_source(sess, "A study of on-demand buses.", scope=scope)
    await tagger().run(apply=True)
    assert (await row(sess, sid)).places == []

    chunk_id = await sess.scalar(select(Chunk.chunk_id).where(Chunk.source_id == sid))
    suffix = uuid.uuid4().hex[:8]
    subject = Entity(canonical_name=f"zz scheme {suffix}", node_type="intervention")
    place = Entity(canonical_name=f"zz place {suffix}", node_type="place", aliases=[SEOUL])
    sess.add_all([subject, place])
    await sess.flush()
    sess.add(
        Edge(
            from_node=subject.entity_id,
            to_node=place.entity_id,
            relation_type="piloted_in",
            supporting_chunk_ids=[chunk_id],
            created_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        )
    )
    await sess.flush()

    stats = await tagger().run(apply=True)

    assert stats.examined == 1
    source = await row(sess, sid)
    assert set(source.places) == {"KR", "KRSEL"}
    assert source.place_evidence["decided"]["KRSEL"] == [ENTITY]
    assert source.place_evidence["entities"] == {"KRSEL": [f"zz place {suffix}"]}


async def test_the_queue_is_a_cursor(world) -> None:
    """A report-only pass writes nothing, so without a cursor it would read the
    first batch forever."""
    sess, _, scope = world
    start = int(await sess.scalar(select(func.coalesce(func.max(Source.source_id), 0))))
    ids = [await a_source(sess, often(JAPAN), scope=scope) for _ in range(3)]
    fp = basis_fingerprint(await load_vocabulary(sess))

    first = await sources_awaiting(sess, fp, limit=2, after=start)
    rest = await sources_awaiting(sess, fp, limit=2, after=first[-1])

    assert first == ids[:2]
    assert rest == ids[2:]


# ---------------------------------------------------------------------------
# The filter
# ---------------------------------------------------------------------------


async def test_the_place_filter_keeps_only_sources_about_the_place(world) -> None:
    sess, tagger, scope = world
    term = f"qxz{uuid.uuid4().hex[:8]}"
    japan = await a_source(sess, f"{term}. " + often(JAPAN), scope=scope)
    seoul = await a_source(sess, f"{term}. " + often(SEOUL), scope=scope)
    unexamined = await a_source(sess, f"{term}. " + often(JAPAN), scope=scope)
    await tagger().run(apply=True)
    await sess.execute(update(Source).where(Source.source_id == unexamined).values(places=None))
    await sess.flush()

    async def found(**filters) -> set[int]:
        result = await search(sess, term, filters=SearchFilters(languages=[scope], **filters))
        return {hit.source_id for hit in result.hits}

    assert await found() == {japan, seoul, unexamined}
    assert await found(places=["JP"]) == {japan}
    # Codes are normalised, as the pass stores them.
    assert await found(places=["jp"]) == {japan}
    # A country finds its city's sources; the city finds only them.
    assert await found(places=["KR"]) == {seoul}
    assert await found(places=["KRSEL"]) == {seoul}
    # Overlap: either place.
    assert await found(places=["JP", "KR"]) == {japan, seoul}
    # A place nothing is about finds nothing, and never the unexamined source.
    assert await found(places=["BR"]) == set()
    result = await search(sess, term, filters=SearchFilters(languages=[scope], places=["JP"]))
    assert all(hit.places == ["JP"] for hit in result.hits)


# ---------------------------------------------------------------------------
# Gaps (§7.2)
# ---------------------------------------------------------------------------


async def test_place_coverage_counts_topic_and_place_together(session_for) -> None:
    sess = await session_for("rw")
    places = {p.code for p in await comparison_set(sess)}
    if not {"SG", "JP"} <= places:
        pytest.skip("needs the seeded source-tier map (make seed)")
    topic = f"zz-place-{uuid.uuid4().hex[:8]}"
    sess.add(TopicConfig(topic=topic, weight=0.0, floor=0.0, ceiling=1.0, status="active"))
    for tagged in (["SG"],) * 3 + (["JP"],):
        sess.add(
            Source(
                url=f"https://{uuid.uuid4().hex[:10]}.test/x",
                checksum=f"sha256:{uuid.uuid4().hex}",
                topic_labels=[topic],
                places=tagged,
                source_tier="government",
            )
        )
    await sess.flush()

    found = {g.id: g for g in await gaps.place_coverage(sess)}

    assert f"place-thin:{topic}:SG" not in found
    thin = found[f"place-thin:{topic}:JP"]
    assert thin.evidence["sources"] == 1
    assert thin.evidence["strong_sources"] == 1
    assert thin.evidence["topic_sources"] == 4
    empty = [
        g
        for g in found.values()
        if g.subject.startswith(f"{topic} ·") and g.evidence["sources"] == 0
    ]
    assert empty, "a comparison place with no sources for the topic is a gap"
    assert all(g.severity > thin.severity for g in empty)
    assert {a.kind for a in thin.actions} == {"seed_query", "open_search"}
    assert all(a.topic == topic for a in thin.actions if a.kind == "seed_query")
    await sess.rollback()


async def test_place_coverage_is_unavailable_until_something_is_examined(session_for) -> None:
    sess = await session_for("rw")
    await sess.execute(update(Source).where(Source.places.is_not(None)).values(places=None))

    with pytest.raises(gaps.SourceUnavailable, match="worker.places"):
        await gaps.place_coverage(sess)
    await sess.rollback()


async def test_place_coverage_without_a_comparison_set_is_unavailable(
    session_for, monkeypatch
) -> None:
    sess = await session_for("rw")

    async def nothing(_sess):
        return []

    monkeypatch.setattr("meridian_core.places.comparison_set", nothing)
    with pytest.raises(gaps.SourceUnavailable, match="comparison set"):
        await gaps.place_coverage(sess)


# ---------------------------------------------------------------------------
# The explore API — committed, because the app reads on its own connection
# ---------------------------------------------------------------------------


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def committed(session_for):
    sess = await session_for("rw")
    marker = f"qxz{uuid.uuid4().hex[:10]}"
    scope = f"zz-{marker[:8]}"
    made = {}
    for key, tagged in (("jp", ["JP"]), ("sg", ["SG"]), ("none", None)):
        source, _ = await upsert_source(
            sess,
            f"https://{marker}.test/{key}",
            checksum=f"sha256:{uuid.uuid4().hex}",
            language=scope,
        )
        source.places = tagged
        await replace_chunks(
            sess, source.source_id, [ChunkWrite(text=f"{marker} passage", chunk_index=0)]
        )
        made[key] = source.source_id
    await sess.commit()
    yield marker, scope, made
    await sess.execute(delete(Source).where(Source.url.like(f"https://{marker}.test/%")))
    await sess.commit()


async def test_the_search_route_applies_the_place_filter(client, committed) -> None:
    marker, scope, made = committed
    everything = await client.get("/api/explore/search", params={"q": marker, "language": scope})
    narrowed = await client.get(
        "/api/explore/search", params={"q": marker, "language": scope, "place": ["JP"]}
    )
    either = await client.get(
        "/api/explore/search",
        params=[("q", marker), ("language", scope), ("place", "JP"), ("place", "SG")],
    )

    assert everything.status_code == narrowed.status_code == either.status_code == 200
    assert {h["source_id"] for h in everything.json()["hits"]} == set(made.values())
    assert [h["source_id"] for h in narrowed.json()["hits"]] == [made["jp"]]
    assert narrowed.json()["hits"][0]["places"] == ["JP"]
    assert {h["source_id"] for h in either.json()["hits"]} == {made["jp"], made["sg"]}


async def test_the_stats_route_offers_the_comparison_set(client, committed) -> None:
    body = (await client.get("/api/explore/stats")).json()

    assert body["sources_without_places"] >= 1  # the fixture's unexamined source
    assert all(set(p) == {"code", "name"} for p in body["places"])
    codes = [p["code"] for p in body["places"]]
    assert len(codes) == len(set(codes))


async def test_the_source_route_exposes_the_evidence(client, committed) -> None:
    _, _, made = committed
    body = (await client.get(f"/api/explore/sources/{made['jp']}")).json()
    assert body["places"] == ["JP"]
    assert {"places_examined_at", "place_basis", "place_evidence"} <= set(body)
