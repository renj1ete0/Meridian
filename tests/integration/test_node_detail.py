"""The node detail panel's data (task P6-04, spec §12.5, §7).

Against Postgres with synthetic graph rows, because the graph itself does not
exist yet — `P4-01` has not run and nothing has ever written an edge. That makes
this the first test file in the repository to put rows in `entities`,
`attribute_values` and `edges`, which is worth saying: it exercises the
provenance constraints those tables carry as much as it exercises the route.

Two properties are the substance.

**A tag carries its confidence and its evidence.** §7 makes confidence
first-class and §2 principle 3 makes a citation mandatory, so an attribute
returned without either is a claim presented as a fact.

**Superseded chunks are visible here and nowhere else.** `P1-32` excludes them
from every other read path, because a superseded chunk is text the page no longer
carries. Here it is the text an attribute was *derived from* — §2.4 re-derives
from source chunks — so a tag whose chunk was replaced by a re-crawl must still
resolve, or the citation goes nowhere.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import (
    AttributeDefinition,
    AttributeValue,
    Chunk,
    Edge,
    Entity,
    Source,
)
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def marker() -> str:
    return f"nd{uuid.uuid4().hex[:10]}"


@pytest.fixture
async def graph(session_for, marker: str):
    """One entity, two attributes, a chunk behind each, and a contested edge.

    Committed, because the app reads on its own connection; deleted afterwards,
    because the dev database is a real corpus somebody else is using.
    """
    sess = await session_for("rw")
    await sess.rollback()

    source, _ = await upsert_source(
        sess,
        f"https://{marker}.test/report",
        checksum=f"sha256:{uuid.uuid4().hex}",
        source_tier="government",
        title="A report",
    )
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=f"{marker} evidence one.", chunk_index=0)],
    )
    await sess.flush()
    chunk = (
        await sess.scalars(
            __import__("sqlalchemy").select(Chunk).where(Chunk.source_id == source.source_id)
        )
    ).one()

    subject = Entity(canonical_name=f"{marker} Subject", node_type="finding")
    other = Entity(canonical_name=f"{marker} Other", node_type="finding")
    sess.add_all([subject, other])
    await sess.flush()

    wide = AttributeDefinition(name=f"{marker}-wide", scope="global")
    narrow = AttributeDefinition(name=f"{marker}-narrow", scope="topic_local", topic="walkability")
    sess.add_all([wide, narrow])
    await sess.flush()

    sess.add_all(
        [
            AttributeValue(
                entity_id=subject.entity_id,
                attribute_id=wide.attribute_id,
                value="low",
                confidence=0.4,
                quality_tier=2,
                supporting_chunk_ids=[chunk.chunk_id],
            ),
            AttributeValue(
                entity_id=subject.entity_id,
                attribute_id=narrow.attribute_id,
                value="high",
                confidence=0.9,
                quality_tier=3,
                supporting_chunk_ids=[chunk.chunk_id],
            ),
        ]
    )
    sess.add(
        Edge(
            from_node=subject.entity_id,
            to_node=other.entity_id,
            relation_type="contradicts",
            contested_with=[999],
            supporting_chunk_ids=[chunk.chunk_id],
        )
    )
    await sess.commit()

    yield sess, subject, source, chunk

    await sess.rollback()
    await sess.execute(delete(Edge).where(Edge.from_node == subject.entity_id))
    await sess.execute(delete(AttributeValue).where(AttributeValue.entity_id == subject.entity_id))
    await sess.execute(
        delete(AttributeDefinition).where(AttributeDefinition.name.like(f"{marker}%"))
    )
    await sess.execute(delete(Entity).where(Entity.canonical_name.like(f"{marker}%")))
    await sess.execute(delete(Source).where(Source.source_id == source.source_id))
    await sess.commit()


async def detail(client, entity_id: int) -> dict:
    return (await client.get(f"/api/explore/nodes/{entity_id}")).json()


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


# --------------------------------------------------------------------------
# The node
# --------------------------------------------------------------------------


async def test_a_missing_node_is_an_answer_not_a_crash(client) -> None:
    response = await client.get("/api/explore/nodes/99999999")

    assert response.status_code == 404


async def test_the_entity_comes_back_with_its_identity(client, graph, marker) -> None:
    _, subject, _, _ = graph

    body = await detail(client, subject.entity_id)

    assert body["entity"]["canonical_name"] == f"{marker} Subject"
    assert body["entity"]["node_type"] == "finding"


async def test_the_embedding_is_not_in_the_payload(client, graph) -> None:
    # 1024 floats per entity, and the panel needs none of them.
    _, subject, _, _ = graph

    assert "embedding" not in await detail(client, subject.entity_id)


# --------------------------------------------------------------------------
# Attribute tags (§7, §12.5)
# --------------------------------------------------------------------------


async def test_a_tag_carries_its_name_rather_than_an_id(client, graph, marker) -> None:
    # A panel showing `attribute_id: 7` asks the reader to resolve a foreign key.
    _, subject, _, _ = graph

    names = {a["name"] for a in (await detail(client, subject.entity_id))["attributes"]}

    assert names == {f"{marker}-wide", f"{marker}-narrow"}


async def test_a_tag_carries_its_confidence(client, graph) -> None:
    # §7 makes confidence first-class. A tag whose confidence a reader cannot
    # see is a claim presented as a fact.
    _, subject, _, _ = graph

    attributes = (await detail(client, subject.entity_id))["attributes"]

    assert all(a["confidence"] is not None for a in attributes)


async def test_tags_are_ordered_by_confidence(client, graph, marker) -> None:
    # Insertion order puts whatever was tagged first at the top, which is a fact
    # about the crawl rather than about the node.
    _, subject, _, _ = graph

    attributes = (await detail(client, subject.entity_id))["attributes"]

    assert [a["name"] for a in attributes] == [f"{marker}-narrow", f"{marker}-wide"]


async def test_a_tag_says_which_scope_it_belongs_to(client, graph) -> None:
    # §7.1: a dimension that applies everywhere and one that applies inside a
    # single topic mean different things, and a flat list implies they do not.
    _, subject, _, _ = graph

    scopes = {a["scope"] for a in (await detail(client, subject.entity_id))["attributes"]}

    assert scopes == {"global", "topic_local"}


# --------------------------------------------------------------------------
# Evidence (§2 principle 3, §12.5)
# --------------------------------------------------------------------------


async def test_the_supporting_chunks_come_back_with_their_source(client, graph) -> None:
    _, subject, source, _ = graph

    supporting = (await detail(client, subject.entity_id))["supporting"]

    assert [hit["source_id"] for hit in supporting] == [source.source_id]
    assert supporting[0]["source_tier"] == "government"
    assert supporting[0]["url"].startswith("https://")


async def test_a_superseded_chunk_is_still_followable_from_a_tag(
    client, graph, marker
) -> None:
    # The one place in the read surface where a superseded chunk is shown, and
    # the reason: this is the text the attribute was derived from. §2.4
    # re-derives from source chunks, so a tag whose chunk was replaced by a
    # re-crawl must still resolve or the citation goes nowhere.
    sess, subject, source, chunk = graph
    await sess.rollback()
    await replace_chunks(sess, source.source_id, [ChunkWrite(text="Rewritten.", chunk_index=0)])
    await sess.commit()

    supporting = (await detail(client, subject.entity_id))["supporting"]

    assert [hit["chunk_id"] for hit in supporting] == [chunk.chunk_id]
    assert supporting[0]["text"] == f"{marker} evidence one."


async def test_supporting_chunks_are_not_ranked(client, graph) -> None:
    # Nothing scored these. A score of 0 beside a search hit's 0.016 would read
    # as a very bad match rather than as a different kind of thing, so the ranks
    # are absent instead.
    _, subject, _, _ = graph

    hit = (await detail(client, subject.entity_id))["supporting"][0]

    assert hit["lexical_rank"] is None
    assert hit["vector_rank"] is None


async def test_a_node_with_no_attributes_returns_no_evidence(client, graph, marker) -> None:
    # An empty panel, not a missing one — and not a 500 from hydrating an empty
    # id list, which is the shape this breaks in.
    sess, _, _, _ = graph
    await sess.rollback()
    bare = Entity(canonical_name=f"{marker} Bare", node_type="finding")
    sess.add(bare)
    await sess.commit()

    body = await detail(client, bare.entity_id)

    assert body["attributes"] == []
    assert body["supporting"] == []


# --------------------------------------------------------------------------
# Contested edges (§9)
# --------------------------------------------------------------------------


async def test_contested_edges_are_counted(client, graph) -> None:
    _, subject, _, _ = graph

    assert (await detail(client, subject.entity_id))["contested_edges"] == 1


async def test_an_uncontested_edge_is_not_counted(client, graph, marker) -> None:
    # The converse: without it the count would pass against an implementation
    # that counted every edge touching the node.
    sess, subject, source, chunk = graph
    await sess.rollback()
    third = Entity(canonical_name=f"{marker} Third", node_type="finding")
    sess.add(third)
    await sess.flush()
    sess.add(
        Edge(
            from_node=subject.entity_id,
            to_node=third.entity_id,
            relation_type="supports",
            supporting_chunk_ids=[chunk.chunk_id],
        )
    )
    await sess.commit()

    assert (await detail(client, subject.entity_id))["contested_edges"] == 1
