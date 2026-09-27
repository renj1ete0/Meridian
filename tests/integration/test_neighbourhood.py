"""A term's neighbourhood against Postgres (task P6-33).

Vectors are built by hand on two axes, so every cosine is known exactly and
each floor is tested from both sides. What these tests protect is the one rule
the task turns on — **cited and similar are different kinds of evidence and
never mix** — and the honest empties: no node named, no stated links, no
vector to measure resemblance from.
"""

from __future__ import annotations

import datetime as dt
import math
import random
import uuid

import pytest
from sqlalchemy import delete, or_, select, update

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.graphview import NodeNotFound
from meridian_core.models import Chunk, Edge, Entity, Observation, Source
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.neighbourhood import (
    MAX_CANDIDATES,
    PASSAGE_FLOOR,
    SIMILAR_FLOOR,
    neighbourhood,
)
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


def vec(cos: float, main: int, other: int) -> list[float]:
    """A unit vector whose cosine with the `main` axis is exactly `cos`."""
    v = [0.0] * EMBEDDING_DIM
    v[main] = cos
    v[other] = math.sqrt(max(0.0, 1.0 - cos * cos))
    return v


class World:
    def __init__(self, marker: str, main: int, other: int) -> None:
        self.marker = marker
        self.main = main
        self.other = other
        self.node: dict[str, Entity] = {}
        self.chunk: dict[str, int] = {}

    def id(self, name: str) -> int:
        return self.node[name].entity_id

    def name(self, name: str) -> str:
        return f"{self.marker} {name}"

    def strip(self, names) -> list[str]:
        return [n.removeprefix(f"{self.marker} ") for n in names]


@pytest.fixture
async def world(session_for):
    sess = await session_for("rw")
    await sess.rollback()
    marker = f"nb{uuid.uuid4().hex[:8]}"
    # Axes of our own, so another test's vectors are orthogonal to ours.
    main, other = random.sample(range(EMBEDDING_DIM), 2)
    w = World(marker, main, other)
    sources: list[int] = []

    async def passage(key: str, cos: float | None, *also: float) -> None:
        source, _ = await upsert_source(
            sess,
            f"https://{marker}-{key}.test/doc",
            checksum=f"sha256:{uuid.uuid4().hex}",
            source_tier="government",
            title=f"{key} document",
            publication_date=dt.date(2023, 1, 1),
        )
        cosines = [cos, *also]
        await replace_chunks(
            sess,
            source.source_id,
            [
                ChunkWrite(text=f"{marker} {key} passage {i}.", chunk_index=i)
                for i in range(len(cosines))
            ],
        )
        await sess.flush()
        chunk_ids = (
            await sess.scalars(
                select(Chunk.chunk_id)
                .where(Chunk.source_id == source.source_id)
                .order_by(Chunk.chunk_index)
            )
        ).all()
        for chunk_id, c in zip(chunk_ids, cosines, strict=True):
            if c is not None:
                await sess.execute(
                    update(Chunk)
                    .where(Chunk.chunk_id == chunk_id)
                    .values(embedding=vec(c, main, other))
                )
        w.chunk[key] = chunk_ids[0]
        for i, chunk_id in enumerate(chunk_ids[1:], start=2):
            w.chunk[f"{key}{i}"] = chunk_id
        sources.append(source.source_id)

    # Two chunks of one document, both near: one voice, shown once.
    await passage("near", 0.80, 0.79)
    await passage("far", PASSAGE_FLOOR - 0.1)
    await passage("gone", 0.99)
    await passage("e1", None)
    await passage("e2", None)
    await passage("e3", None)
    # The page changed: "gone" is text it no longer carries.
    gone_source = sources[2]
    await replace_chunks(sess, gone_source, [])

    def entity(name: str, cos: float | None, **kw) -> None:
        w.node[name] = Entity(
            canonical_name=w.name(name),
            node_type=kw.pop("node_type", "concept"),
            embedding=vec(cos, main, other) if cos is not None else None,
            **kw,
        )

    entity("Covered Walkways", 1.0, aliases=[f"{marker}-CW"])
    entity("Canopy", 0.95)  # cited *and* similar: inner ring only
    entity("Heat", None)
    entity("Town", None, node_type="place")
    entity("Shelter", 0.90)
    entity("Awning", SIMILAR_FLOOR + 0.01)
    entity("Faint", SIMILAR_FLOOR - 0.05)
    entity("Note", 0.99, is_annotation=True)
    entity("Gone", 0.98)
    entity("Lonely", 0.30)
    entity("Unvectored", None)
    sess.add_all(w.node.values())
    await sess.flush()
    w.node["Gone"].redirects_to = w.id("Canopy")

    c = w.chunk
    edges = [
        Edge(
            from_node=w.id("Covered Walkways"),
            to_node=w.id("Canopy"),
            relation_type="reduces",
            supporting_chunk_ids=[c["e1"], c["e2"]],
        ),
        Edge(
            from_node=w.id("Heat"),
            to_node=w.id("Covered Walkways"),
            relation_type="obstructs",
            supporting_chunk_ids=[c["e3"]],
        ),
        Edge(
            from_node=w.id("Covered Walkways"),
            to_node=w.id("Gone"),
            relation_type="increases",
            supporting_chunk_ids=[c["e1"]],
        ),
    ]
    sess.add_all(edges)
    await sess.flush()
    edges[1].contested_with = [edges[0].edge_id]
    sess.add(
        Observation(
            subject_entity_id=w.id("Covered Walkways"),
            metric=f"{marker}_length",
            value_numeric=3.0,
            unit="km",
            geography_entity_id=w.id("Town"),
            supporting_chunk_ids=[c["e2"]],
        )
    )
    await sess.commit()

    yield w

    await sess.rollback()
    ids = [e.entity_id for e in w.node.values()]
    await sess.execute(delete(Observation).where(Observation.subject_entity_id.in_(ids)))
    await sess.execute(delete(Edge).where(or_(Edge.from_node.in_(ids), Edge.to_node.in_(ids))))
    await sess.execute(update(Entity).where(Entity.entity_id.in_(ids)).values(redirects_to=None))
    await sess.execute(delete(Entity).where(Entity.entity_id.in_(ids)))
    await sess.execute(delete(Source).where(Source.source_id.in_(sources)))
    await sess.commit()


def fixed(vector: list[float]):
    async def embed(_text: str) -> list[float]:
        return vector

    return embed


async def ro(session_for):
    return await session_for("ro")


# --------------------------------------------------------------------------
# The anchor: by name, never by meaning
# --------------------------------------------------------------------------


async def test_a_plural_or_differently_cased_term_names_the_node(session_for, world) -> None:
    sess = await ro(session_for)
    body = await neighbourhood(sess, world.name("covered walkway"))
    assert body.anchor is not None and body.anchor.entity_id == world.id("Covered Walkways")
    assert body.candidates == []


async def test_an_alias_names_the_node(session_for, world) -> None:
    sess = await ro(session_for)
    body = await neighbourhood(sess, f"{world.marker}-cw")
    assert body.anchor is not None and body.anchor.entity_id == world.id("Covered Walkways")


async def test_a_term_that_names_no_node_has_no_anchor_and_offers_candidates(
    session_for, world
) -> None:
    """Rejection: a substring is not a name, however close in meaning."""
    sess = await ro(session_for)
    body = await neighbourhood(
        sess, world.name("covered"), embed=fixed(vec(1.0, world.main, world.other))
    )
    assert body.anchor is None
    assert body.cited == [] and body.cited_total == 0
    assert world.id("Covered Walkways") in [t.entity_id for t in body.candidates]
    # The outer ring still answers, measured from the term as typed.
    assert body.similar_basis == "term"
    assert world.id("Covered Walkways") in [s.entity_id for s in body.similar]


async def test_a_question_offers_the_nodes_its_words_name(session_for, world) -> None:
    """`B-94`: the whole question names nothing, so its words are looked up."""
    sess = await ro(session_for)
    question = f"how does {world.marker}-canopy relate to {world.name('covered')} streets"
    body = await neighbourhood(sess, question)
    assert body.anchor is None
    offered = [t.entity_id for t in body.candidates]
    assert world.id("Covered Walkways") in offered
    assert len(offered) == len(set(offered)), "a node offered twice"
    assert len(offered) <= MAX_CANDIDATES


async def test_a_question_made_only_of_question_words_offers_nothing(session_for, world) -> None:
    """Rejection: "does" and "with" are inside many names and name none of them."""
    sess = await ro(session_for)
    body = await neighbourhood(sess, "what does this have to do with that")
    assert body.anchor is None and body.candidates == []


async def test_a_merged_node_is_never_the_anchor(session_for, world) -> None:
    sess = await ro(session_for)
    body = await neighbourhood(sess, world.name("Gone"))
    assert body.anchor is None


async def test_picking_a_merged_node_follows_its_redirect(session_for, world) -> None:
    sess = await ro(session_for)
    body = await neighbourhood(sess, "", entity_id=world.id("Gone"))
    assert body.anchor.entity_id == world.id("Canopy")


async def test_an_unknown_node_is_refused(session_for, world) -> None:
    sess = await ro(session_for)
    with pytest.raises(NodeNotFound):
        await neighbourhood(sess, "", entity_id=-1)


# --------------------------------------------------------------------------
# The inner ring: what a passage states
# --------------------------------------------------------------------------


async def test_inner_ring_is_edges_both_ways_and_observed_places(session_for, world) -> None:
    sess = await ro(session_for)
    body = await neighbourhood(sess, world.name("Covered Walkways"))
    cited = {c.canonical_name.removeprefix(f"{world.marker} "): c for c in body.cited}
    assert set(cited) == {"Canopy", "Heat", "Town"}
    assert body.cited_total == 3
    assert cited["Canopy"].support == 2
    assert [(r.relation_type, r.outgoing) for r in cited["Canopy"].relations] == [("reduces", True)]
    # Stored the other way round: the anchor is the object, not the subject.
    assert [(r.relation_type, r.outgoing) for r in cited["Heat"].relations] == [
        ("obstructs", False)
    ]
    assert cited["Heat"].contested is True
    assert cited["Canopy"].contested is False
    assert cited["Town"].relations[0].relation_type.startswith("measured in:")
    # Ranked by support, most first.
    assert body.cited[0].entity_id == world.id("Canopy")


async def test_a_merged_neighbour_is_not_in_the_inner_ring(session_for, world) -> None:
    sess = await ro(session_for)
    body = await neighbourhood(sess, world.name("Covered Walkways"))
    assert world.id("Gone") not in [c.entity_id for c in body.cited]


async def test_a_node_with_no_stated_links_says_so_and_keeps_the_outer_ring(
    session_for, world
) -> None:
    """The sparse-graph case: an anchor, zero claims, resemblance still shown."""
    sess = await ro(session_for)
    body = await neighbourhood(sess, world.name("Shelter"))
    assert body.anchor.entity_id == world.id("Shelter")
    assert body.cited == [] and body.cited_total == 0
    assert body.similar_basis == "node"
    assert world.id("Covered Walkways") in [s.entity_id for s in body.similar]


# --------------------------------------------------------------------------
# The outer ring: what reads alike, kept apart
# --------------------------------------------------------------------------


async def test_outer_ring_excludes_cited_anchor_notes_merged_and_the_faint(
    session_for, world
) -> None:
    sess = await ro(session_for)
    body = await neighbourhood(sess, world.name("Covered Walkways"))
    similar = world.strip(s.canonical_name for s in body.similar)
    assert similar == ["Shelter", "Awning"]
    assert all(s.similarity >= SIMILAR_FLOOR for s in body.similar)
    assert body.similar[0].similarity == pytest.approx(0.90, abs=1e-4)
    # Cited and similar never share a node.
    assert not {c.entity_id for c in body.cited} & {s.entity_id for s in body.similar}


async def test_passages_near_in_meaning_obey_search_filters_and_the_floor(
    session_for, world
) -> None:
    sess = await ro(session_for)
    body = await neighbourhood(sess, world.name("Covered Walkways"))
    ids = [p.hit.chunk_id for p in body.passages]
    assert world.chunk["near"] in ids
    assert world.chunk["near2"] not in ids
    assert len({p.hit.source_id for p in body.passages}) == len(body.passages)
    # Superseded text is never quoted; below the floor is not shown.
    assert world.chunk["gone"] not in ids
    assert world.chunk["far"] not in ids
    assert all(p.similarity >= PASSAGE_FLOOR for p in body.passages)


async def test_no_vector_anywhere_means_the_outer_ring_was_not_computed(session_for, world) -> None:
    sess = await ro(session_for)
    body = await neighbourhood(sess, world.name("Unvectored"))
    assert body.anchor.entity_id == world.id("Unvectored")
    assert body.similar_basis == "none"
    assert body.similar == [] and body.passages == []


async def test_the_term_is_embedded_only_when_the_anchor_has_no_vector(session_for, world) -> None:
    calls: list[str] = []

    async def embed(text: str) -> list[float]:
        calls.append(text)
        return vec(1.0, world.main, world.other)

    sess = await ro(session_for)
    body = await neighbourhood(sess, world.name("Covered Walkways"), embed=embed)
    assert calls == [] and body.similar_basis == "node"
    body = await neighbourhood(sess, world.name("Unvectored"), embed=embed)
    assert calls == [world.name("Unvectored")] and body.similar_basis == "term"


# --------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------


@pytest.fixture
async def client():
    import httpx

    from api.main import create_app
    from meridian_core.db import dispose_engines

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


async def test_route_answers_a_term(client, world) -> None:
    response = await client.get("/api/explore/neighbourhood", params={"q": world.name("canopies")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["anchor"]["entity_id"] == world.id("Canopy")
    assert set(body) >= {"cited", "similar", "passages", "similar_basis", "similar_floor"}


async def test_route_refuses_a_request_with_nothing_to_answer(client) -> None:
    response = await client.get("/api/explore/neighbourhood", params={"q": "   "})
    assert response.status_code == 422


async def test_route_names_a_missing_node(client) -> None:
    response = await client.get("/api/explore/neighbourhood", params={"entity_id": -5})
    assert response.status_code == 404
    assert "-5" in response.json()["detail"]
