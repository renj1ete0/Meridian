"""Bridges between areas against a real Postgres (task P6-31).

Three kinds, kept apart: a cited claim (an edge whose evidence spans two
areas), similar passages across them, shared terms. The tests hold each kind
to its definition, and hold a reader's own note out of "cited".
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import numpy as np
import pytest
from area_doubles import a_topic, seed, shrink_levels
from sqlalchemy import delete, select

from api.main import create_app
from meridian_core.areabuild import build_areas
from meridian_core.areaview import AreaNotFound, areas_level
from meridian_core.bridgeview import bridge
from meridian_core.db import dispose_engines
from meridian_core.models import (
    Area,
    AreaBridge,
    AreaBuild,
    AreaMember,
    Chunk,
    Edge,
    Entity,
    Source,
)

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture(autouse=True)
def small_levels(monkeypatch):
    shrink_levels(monkeypatch)


@pytest.fixture
def topic() -> str:
    return a_topic()


async def graph(sess, chunks: dict[int, int]) -> dict[str, int]:
    """Two concepts anchored in subjects 0 and 1, three edges between them.

    ``spans``: one edge citing passages in subjects 0 and 2 — its own evidence
    spans two areas. ``anchored``: citing one subject-0 passage, but linking a
    concept anchored in 0 to one anchored in 1. ``note``: a reader's
    annotation linking to the second concept — never a cited claim.
    """
    by_subject = {s: sorted(c for c, x in chunks.items() if x == s) for s in (0, 1, 2)}
    shade = Entity(
        canonical_name="shade", node_type="concept", supporting_chunk_ids=by_subject[0][:5]
    )
    enzyme = Entity(
        canonical_name="enzyme", node_type="concept", supporting_chunk_ids=by_subject[1][:5]
    )
    note = Entity(
        canonical_name="my note", node_type="concept", is_annotation=True, supporting_chunk_ids=[]
    )
    sess.add_all([shade, enzyme, note])
    await sess.flush()
    spans = Edge(
        from_node=shade.entity_id,
        to_node=enzyme.entity_id,
        relation_type="relates_to",
        supporting_chunk_ids=[by_subject[0][0], by_subject[2][0]],
    )
    anchored = Edge(
        from_node=shade.entity_id,
        to_node=enzyme.entity_id,
        relation_type="increases",
        supporting_chunk_ids=[by_subject[0][1]],
    )
    annotates = Edge(
        from_node=note.entity_id,
        to_node=enzyme.entity_id,
        relation_type="annotates",
        supporting_chunk_ids=[by_subject[1][0], by_subject[0][2]],
    )
    sess.add_all([spans, anchored, annotates])
    await sess.flush()
    return {"spans": spans.edge_id, "anchored": anchored.edge_id, "note": annotates.edge_id}


async def leaves_of(sess, build_id: int) -> dict[int, int]:
    return dict(
        (
            await sess.execute(
                select(AreaMember.chunk_id, AreaMember.area_id).where(
                    AreaMember.build_id == build_id
                )
            )
        ).all()
    )


async def chain(sess, leaf: int) -> dict[int, int]:
    """``{level: area_id}`` from a leaf up."""
    out = {}
    area = await sess.get(Area, leaf)
    while area is not None:
        out[area.level] = area.area_id
        area = await sess.get(Area, area.parent_id) if area.parent_id else None
    return out


async def test_cited_bridges_follow_evidence_and_leave_notes_out(session_for, topic):
    sess = await session_for("rw")
    chunks = await seed(sess, topic)
    edges = await graph(sess, chunks)
    report = await build_areas(sess, topics=[topic])
    assert report.bridges > 0

    rows = list(
        await sess.scalars(select(AreaBridge).where(AreaBridge.build_id == report.build_id))
    )
    cited = {e for row in rows for e in row.cited_edge_ids}
    assert edges["note"] not in cited, "a reader's note is not a claim a source makes"
    assert {edges["spans"], edges["anchored"]} <= cited

    leaves = await leaves_of(sess, report.build_id)
    subject_chain = {
        s: await chain(sess, leaves[next(c for c, x in chunks.items() if x == s)]) for s in (0, 1)
    }
    # Where the two subjects first part, their areas are siblings, and the
    # anchored edge bridges exactly them.
    level = next(lv for lv in (1, 2, 3) if subject_chain[0][lv] != subject_chain[1][lv])
    pair = tuple(sorted((subject_chain[0][level], subject_chain[1][level])))
    row = next(r for r in rows if (r.area_a, r.area_b) == pair)
    assert edges["anchored"] in row.cited_edge_ids
    assert row.cited_sources >= 1

    for row in rows:
        a, b = await sess.get(Area, row.area_a), await sess.get(Area, row.area_b)
        assert row.area_a < row.area_b
        assert a.level == b.level == row.level and a.parent_id == b.parent_id, "siblings only"


async def test_similar_pairs_are_real_passages_of_each_side(session_for, topic):
    sess = await session_for("rw")
    await seed(sess, topic)
    report = await build_areas(sess, topics=[topic])
    leaves = await leaves_of(sess, report.build_id)
    areas = {
        a.area_id: a
        for a in await sess.scalars(select(Area).where(Area.build_id == report.build_id))
    }

    def under(area_id: int, leaf: int) -> bool:
        node = areas[leaf]
        while node is not None:
            if node.area_id == area_id:
                return True
            node = areas.get(node.parent_id) if node.parent_id else None
        return False

    rows = list(
        await sess.scalars(select(AreaBridge).where(AreaBridge.build_id == report.build_id))
    )
    assert rows and all(row.similar_pairs for row in rows)
    for row in rows:
        seen = [p["chunk_a"] for p in row.similar_pairs] + [p["chunk_b"] for p in row.similar_pairs]
        assert len(seen) == len(set(seen)), "no passage used twice in one bridge"
        scores = [p["score"] for p in row.similar_pairs]
        assert scores == sorted(scores, reverse=True)
        for p in row.similar_pairs:
            assert under(row.area_a, leaves[p["chunk_a"]]) and under(
                row.area_b, leaves[p["chunk_b"]]
            )
            va = np.asarray((await sess.get(Chunk, p["chunk_a"])).embedding, dtype=np.float64)
            vb = np.asarray((await sess.get(Chunk, p["chunk_b"])).embedding, dtype=np.float64)
            cosine = float(va @ vb / (np.linalg.norm(va) * np.linalg.norm(vb)))
            assert abs(cosine - p["score"]) < 1e-3


async def test_levels_carry_their_links_and_the_bridge_reads_in_three_kinds(session_for, topic):
    sess = await session_for("rw")
    chunks = await seed(sess, topic)
    edges = await graph(sess, chunks)
    await build_areas(sess, topics=[topic])

    top = await areas_level(sess)
    ids = {a.area_id for a in top.areas}
    assert top.links and all({link.area_a, link.area_b} <= ids for link in top.links)

    everything = [top]
    for region in top.areas:
        everything.append(await areas_level(sess, parent_id=region.area_id))
    link = next(link for level in everything for link in level.links if link.cited_claims)
    read = await bridge(sess, link.area_a, link.area_b)
    assert len(read.claims) == link.cited_claims
    assert {c.edge_id for c in read.claims} <= {edges["spans"], edges["anchored"]}
    assert all(c.from_name and c.to_name and c.citations >= 1 for c in read.claims)
    assert len(read.similar) == link.similar_pairs
    assert read.a.area_id == link.area_a and read.b.area_id == link.area_b

    flipped = await bridge(sess, link.area_b, link.area_a)
    assert [p.a.chunk_id for p in flipped.similar] == [p.b.chunk_id for p in read.similar]
    assert flipped.a.area_id == link.area_b


async def test_a_pair_with_nothing_recorded_is_an_empty_answer(session_for, topic):
    sess = await session_for("rw")
    await seed(sess, topic)
    report = await build_areas(sess, topics=[topic])
    row = await sess.scalar(select(AreaBridge).where(AreaBridge.build_id == report.build_id))
    await sess.execute(delete(AreaBridge).where(AreaBridge.bridge_id == row.bridge_id))

    read = await bridge(sess, row.area_a, row.area_b)
    assert read.claims == [] and read.similar == [] and read.shared_terms == []


async def test_a_bridge_needs_two_areas_of_the_current_map(session_for, topic):
    sess = await session_for("rw")
    await seed(sess, topic)
    old = await build_areas(sess, topics=[topic])
    await build_areas(sess, topics=[topic])
    stale = list(await sess.scalars(select(Area.area_id).where(Area.build_id == old.build_id)))

    with pytest.raises(ValueError):
        await bridge(sess, stale[0], stale[0])
    with pytest.raises(AreaNotFound, match="earlier build"):
        await bridge(sess, stale[0], stale[1])


async def test_the_database_refuses_a_backwards_or_duplicate_pair(session_for, topic):
    from sqlalchemy.exc import IntegrityError

    sess = await session_for("rw")
    await seed(sess, topic)
    report = await build_areas(sess, topics=[topic])
    row = await sess.scalar(select(AreaBridge).where(AreaBridge.build_id == report.build_id))

    def twin(a: int, b: int) -> AreaBridge:
        return AreaBridge(
            build_id=row.build_id,
            level=row.level,
            area_a=a,
            area_b=b,
            similarity=0.5,
            cited_edge_ids=[],
            cited_sources=0,
            similar_pairs=[],
            shared_terms=[],
        )

    for a, b in ((row.area_b, row.area_a), (row.area_a, row.area_b)):
        nested = await sess.begin_nested()
        sess.add(twin(a, b))
        with pytest.raises(IntegrityError):
            await sess.flush()
        await nested.rollback()


# ---------------------------------------------------------------------------


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def committed(session_for, topic):
    sess = await session_for("rw")
    chunks = await seed(sess, topic)
    await graph(sess, chunks)
    report = await build_areas(sess, topics=[topic])
    await sess.commit()
    yield report
    await sess.execute(delete(AreaBuild).where(AreaBuild.build_id == report.build_id))
    await sess.execute(
        delete(Edge).where(
            Edge.from_node.in_(
                select(Entity.entity_id).where(
                    Entity.canonical_name.in_(["shade", "enzyme", "my note"])
                )
            )
        )
    )
    await sess.execute(delete(Source).where(Source.url.like(f"https://{topic}.test/%")))
    await sess.commit()


async def test_the_api_reads_a_bridge_and_refuses_what_is_not_one(client, committed, session_for):
    sess = await session_for("ro")
    pair = (
        await sess.execute(
            select(AreaBridge.area_a, AreaBridge.area_b).where(
                AreaBridge.build_id == committed.build_id
            )
        )
    ).first()
    body = (await client.get(f"/api/explore/bridges/{pair[0]}/{pair[1]}")).json()
    assert set(body) == {
        "a",
        "b",
        "claims",
        "cited_sources",
        "similar",
        "shared_terms",
        "similarity",
    }

    top = (await client.get("/api/explore/areas")).json()
    assert all(set(link) >= {"cited_claims", "similar_pairs"} for link in top["links"])

    assert (await client.get(f"/api/explore/bridges/{pair[0]}/{pair[0]}")).status_code == 422
    assert (await client.get("/api/explore/bridges/999999999998/999999999999")).status_code == 404


def test_the_three_kinds_are_three_columns():
    """Drift: the kinds must stay separate columns, not one merged score."""
    columns = {c.name for c in AreaBridge.__table__.columns}
    assert {"cited_edge_ids", "similar_pairs", "shared_terms"} <= columns
    assert not any("score" in c for c in columns), "no merged strength column"


async def test_nearest_in_a_subset_never_rides_the_vector_index(session_for):
    """Handover, "An HNSW scan returns at most ef_search rows": an index scan
    filtered to one area's passages returns whatever few of the corpus-wide
    nearest belong to it — in the full suite, none. The sort key must be one
    the index cannot serve."""
    from sqlalchemy import text
    from sqlalchemy.dialects import postgresql

    from meridian_core.bridges import exact_distance

    compiled = str(
        exact_distance(Chunk.embedding, [0.0] * 1024).compile(dialect=postgresql.dialect())
    )
    assert "<=>" in compiled and "+" in compiled

    sess = await session_for("rw")
    await sess.execute(text("SET LOCAL enable_seqscan = off"))
    vector = "'[" + ",".join(["1"] + ["0"] * 1023) + "]'"

    async def plan(order: str) -> str:
        rows = await sess.scalars(
            text(f"EXPLAIN SELECT chunk_id FROM chunks ORDER BY {order} LIMIT 5")
        )
        return "\n".join(rows)

    # The control: the indexed expression (half precision since `B-136`) does use the index,
    # so the test would see a regression.
    indexed = f"embedding::halfvec(1024) <=> {vector}::halfvec(1024)"
    assert "ix_chunks_embedding_hnsw_half" in await plan(indexed)
    assert "Index Scan" not in await plan(f"(embedding <=> {vector}) + 0")
    assert "Index Scan" not in await plan(f"({indexed}) + 0")
