"""Routes across claims and resemblance, against Postgres (task P6-32).

Vectors are drawn on axis pairs of the test's own, so every cosine is the
cosine of an angle the fixture chose. The properties under test:

- every hop is labelled cited or similar, and the counts agree with the labels;
- the claims-only answer is always there, and "no cited route within N hops"
  is a result, not an error;
- among equally short routes, claims beat resemblance;
- merged nodes and a reader's notes are never stops;
- a term that names no node joins only by resemblance, and the claims-only
  answer says why it cannot be reached.
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
from meridian_core.models import Chunk, Edge, Entity, Source
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.route import CitedHops, Endpoint, SimilarHops, route, search
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


def at(degrees: float, axes: tuple[int, int]) -> list[float]:
    """A unit vector at `degrees` in the plane of two axes."""
    v = [0.0] * EMBEDDING_DIM
    v[axes[0]] = math.cos(math.radians(degrees))
    v[axes[1]] = math.sin(math.radians(degrees))
    return v


class World:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.node: dict[str, Entity] = {}
        self.axes: dict[str, tuple[int, int]] = {}
        self.edge: dict[str, Edge] = {}
        self.chunk: list[int] = []

    def id(self, name: str) -> int:
        return self.node[name].entity_id

    def name(self, name: str) -> str:
        return f"{self.marker} {name}"

    def end(self, name: str) -> Endpoint:
        return Endpoint(entity_id=self.id(name))


@pytest.fixture
async def world(session_for):
    sess = await session_for("rw")
    await sess.rollback()
    marker = f"rt{uuid.uuid4().hex[:8]}"
    w = World(marker)
    picked = random.sample(range(EMBEDDING_DIM), 4)
    w.axes = {"p": (picked[0], picked[1]), "q": (picked[2], picked[3])}

    source, _ = await upsert_source(
        sess,
        f"https://{marker}.test/doc",
        checksum=f"sha256:{uuid.uuid4().hex}",
        source_tier="government",
        title="route document",
        publication_date=dt.date(2023, 1, 1),
    )
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=f"{marker} passage {i}.", chunk_index=i) for i in range(3)],
    )
    await sess.flush()
    w.chunk = list(
        (
            await sess.scalars(
                select(Chunk.chunk_id)
                .where(Chunk.source_id == source.source_id)
                .order_by(Chunk.chunk_index)
            )
        ).all()
    )

    def entity(name: str, angle: float | None = None, axes: str = "p", **kw) -> None:
        w.node[name] = Entity(
            canonical_name=w.name(name),
            node_type="concept",
            embedding=at(angle, w.axes[axes]) if angle is not None else None,
            **kw,
        )

    # A cited chain A-B-C, and D, which only reads like C.
    entity("A")
    entity("B")
    entity("C", 0)
    entity("D", 20)
    # Nothing reaches E except through a merged node or a note.
    entity("E")
    entity("Merged")
    entity("Note", is_annotation=True)
    # S to T: two cited hops through M, or two similar hops through N.
    entity("S", 0, "q")
    entity("M")
    entity("N", 30, "q")
    entity("T", 60, "q")
    sess.add_all(w.node.values())
    await sess.flush()
    w.node["Merged"].redirects_to = w.id("A")

    c = w.chunk

    def edge(key: str, a: str, b: str, chunks: list[int], **kw) -> None:
        w.edge[key] = Edge(
            from_node=w.id(a),
            to_node=w.id(b),
            relation_type=kw.pop("relation_type", "increases"),
            supporting_chunk_ids=chunks,
            **kw,
        )

    edge("AB", "A", "B", [c[0], c[1]])
    # Stored against the direction of travel from A.
    edge("CB", "C", "B", [c[2]], relation_type="requires")
    edge("AMerged", "A", "Merged", [c[0]])
    edge("MergedE", "Merged", "E", [c[0]])
    edge("ANote", "A", "Note", [c[0]], relation_type="annotates")
    edge("NoteE", "Note", "E", [c[0]], relation_type="annotates")
    edge("SM", "S", "M", [c[0]])
    edge("MT", "M", "T", [c[1]])
    sess.add_all(w.edge.values())
    await sess.commit()

    yield w

    await sess.rollback()
    ids = [e.entity_id for e in w.node.values()]
    await sess.execute(delete(Edge).where(or_(Edge.from_node.in_(ids), Edge.to_node.in_(ids))))
    await sess.execute(update(Entity).where(Entity.entity_id.in_(ids)).values(redirects_to=None))
    await sess.execute(delete(Entity).where(Entity.entity_id.in_(ids)))
    await sess.execute(delete(Source).where(Source.source_id == source.source_id))
    await sess.commit()


async def ro(session_for):
    return await session_for("ro")


def names(w: World, body) -> list[str]:
    return [s.name.removeprefix(f"{w.marker} ") for s in body.stops]


# --------------------------------------------------------------------------
# Labels, and the claims-only answer beside the mixed one
# --------------------------------------------------------------------------


async def test_a_cited_route_is_labelled_cited_with_direction_and_evidence(
    session_for, world
) -> None:
    body = await route(await ro(session_for), world.end("A"), world.end("C"))
    assert body.found and body.hops == 2
    assert names(world, body) == ["A", "B", "C"]
    assert [h.kind for h in body.route] == ["cited", "cited"]
    assert (body.cited_hops, body.similar_hops) == (2, 0)
    first, second = body.route
    assert (first.relation_type, first.forward, first.support) == ("increases", True, 2)
    # The edge is stored C -> B, so it runs against the route.
    assert (second.relation_type, second.forward) == ("requires", False)
    assert first.evidence is not None and first.evidence.chunk_id == world.chunk[0]
    assert first.similarity is None
    assert body.cited_only.found and body.cited_only.hops == 2


async def test_resemblance_extends_a_route_and_is_labelled_as_such(session_for, world) -> None:
    body = await route(await ro(session_for), world.end("A"), world.end("D"))
    assert names(world, body) == ["A", "B", "C", "D"]
    assert [h.kind for h in body.route] == ["cited", "cited", "similar"]
    last = body.route[-1]
    assert last.similarity == pytest.approx(math.cos(math.radians(20)), abs=1e-3)
    assert last.edge_id is None and last.evidence is None
    assert (body.cited_hops, body.similar_hops) == (2, 1)


async def test_no_cited_route_is_a_result_carried_beside_the_mixed_one(session_for, world) -> None:
    """The finding the Gaps screen reads: a route exists only by resemblance."""
    body = await route(await ro(session_for), world.end("A"), world.end("D"))
    assert body.found is True
    assert body.cited_only.found is False and body.cited_only.hops is None
    assert body.cited_only.reason is None


async def test_claims_only_refuses_resemblance(session_for, world) -> None:
    body = await route(await ro(session_for), world.end("A"), world.end("D"), allow="cited")
    assert body.found is False and body.route == [] and body.stops == []
    assert body.cited_only.found is False


async def test_the_depth_bound_is_honoured(session_for, world) -> None:
    body = await route(await ro(session_for), world.end("A"), world.end("D"), max_depth=2)
    assert body.found is False and body.max_depth == 2


async def test_claims_beat_resemblance_between_equally_short_routes(session_for, world) -> None:
    body = await route(await ro(session_for), world.end("S"), world.end("T"))
    assert names(world, body) == ["S", "M", "T"]
    assert body.similar_hops == 0


async def test_resemblance_alone_is_used_when_the_claims_are_absent(session_for, world) -> None:
    sess = await ro(session_for)
    s, t = ("entity", world.id("S")), ("entity", world.id("T"))
    found, truncated = await search(sess, s, t, [SimilarHops()], max_depth=3)
    assert found is not None and truncated is False
    assert [h.kind for h in found.hops] == ["similar", "similar"]
    # And with the floor above both steps, there is no route at all.
    found, _ = await search(sess, s, t, [SimilarHops(floor=0.9)], max_depth=3)
    assert found is None


# --------------------------------------------------------------------------
# What a route never stops at
# --------------------------------------------------------------------------


async def test_merged_nodes_and_notes_are_never_stops(session_for, world) -> None:
    body = await route(await ro(session_for), world.end("A"), world.end("E"))
    assert body.found is False
    assert body.cited_only.found is False


async def test_a_merged_end_follows_its_redirect(session_for, world) -> None:
    body = await route(await ro(session_for), world.end("Merged"), world.end("B"))
    assert body.source.id == world.id("A")
    assert body.hops == 1


async def test_an_unknown_node_is_refused(session_for, world) -> None:
    with pytest.raises(NodeNotFound):
        await route(await ro(session_for), Endpoint(entity_id=-1), world.end("A"))


async def test_the_same_end_twice_is_a_route_of_no_hops(session_for, world) -> None:
    body = await route(await ro(session_for), world.end("A"), world.end("A"))
    assert body.found and body.hops == 0 and body.route == []


# --------------------------------------------------------------------------
# Terms
# --------------------------------------------------------------------------


async def test_a_term_that_names_a_node_is_that_node(session_for, world) -> None:
    body = await route(
        await ro(session_for), world.end("A"), Endpoint(term=world.name("c").upper())
    )
    assert body.target.kind == "entity" and body.target.id == world.id("C")
    assert body.cited_only.found


async def test_a_term_that_names_no_node_joins_by_resemblance_only(session_for, world) -> None:
    async def embed(_text: str) -> list[float]:
        return at(10, world.axes["p"])

    term = f"{world.marker} something unnamed"
    body = await route(await ro(session_for), world.end("A"), Endpoint(term=term), embed=embed)
    assert body.target.kind == "term" and body.target.id is None
    assert body.found
    assert [h.kind for h in body.route] == ["cited", "cited", "similar"]
    assert body.stops[-1].name == term
    assert body.cited_only.found is False
    assert "names no node" in body.cited_only.reason


async def test_a_term_with_no_vector_cannot_be_reached(session_for, world) -> None:
    body = await route(
        await ro(session_for), world.end("A"), Endpoint(term=f"{world.marker} unnamed")
    )
    assert body.found is False


async def test_cited_hops_only_leave_entities(session_for, world) -> None:
    hops = await CitedHops().hops(await ro(session_for), [("term", "anything")])
    assert hops == []


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


async def test_route_answers_two_nodes(client, world) -> None:
    response = await client.get(
        "/api/explore/route", params={"source": world.id("A"), "target": world.id("D")}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [h["kind"] for h in body["route"]] == ["cited", "cited", "similar"]
    assert body["cited_only"]["found"] is False


async def test_route_takes_a_term_for_an_end(client, world) -> None:
    response = await client.get(
        "/api/explore/route", params={"source": world.id("A"), "target_q": world.name("C")}
    )
    assert response.status_code == 200, response.text
    assert response.json()["target"]["id"] == world.id("C")


async def test_route_refuses_a_missing_end_and_a_bad_depth(client, world) -> None:
    assert (await client.get("/api/explore/route", params={"source": 1})).status_code == 422
    too_deep = {"source": world.id("A"), "target": world.id("B"), "max_depth": 99}
    assert (await client.get("/api/explore/route", params=too_deep)).status_code == 422
    missing = await client.get("/api/explore/route", params={"source": -3, "target": -4})
    assert missing.status_code == 404 and "-3" in missing.json()["detail"]
