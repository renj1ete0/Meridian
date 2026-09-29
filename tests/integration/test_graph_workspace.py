"""The graph workspace's reads (tasks P6-01, P6-02, P6-03; spec §12.2, §12.3).

Against Postgres with a synthetic neighbourhood, because what these routes get
wrong is SQL: an `OR` across both edge directions, an array overlap, a window
function picking hints, a breadth-first search that walks into a merged node.

The properties under test are the spec's, transcribed:

- **§12.2, never render the whole graph.** Depth 1 only, capped, ranked — and
  the response says how many were left out, so a capped view cannot pass for
  the whole neighbourhood.
- **§12.2's live filters act on evidence.** An edge survives when one passage
  satisfies every filter at once. Most tests here are rejection tests: the edge
  that must *not* come back.
- **§5.5, a merged node is a redirect.** It never appears as a neighbour, a
  hint, a search match or a stop on a path.
- **§12.2 path mode.** A route, bounded; "none within N hops" is an answer.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, or_, select

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.graphview import MAX_CONTESTED, MAX_NEIGHBOURS, edge_passes, is_cross_topic
from meridian_core.models import (
    AttributeDefinition,
    AttributeValue,
    Chunk,
    Edge,
    Entity,
    Source,
)
from meridian_core.models.source import SOURCE_TIER
from meridian_core.schemas.graphview import GraphFilters
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def marker() -> str:
    return f"gw{uuid.uuid4().hex[:10]}"


class World:
    """Names for the rows the fixture made, so tests read as the graph does."""

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.node: dict[str, Entity] = {}
        self.edge: dict[str, Edge] = {}
        self.chunk: dict[str, int] = {}

    def id(self, name: str) -> int:
        return self.node[name].entity_id


@pytest.fixture
async def world(session_for, marker: str):
    """A focus with seven neighbours, hints past one of them, and two paths.

    Committed, because the app reads on its own connection; deleted afterwards,
    because the dev database is a corpus somebody else is using.
    """
    sess = await session_for("rw")
    await sess.rollback()
    w = World(marker)
    sources: list[int] = []

    async def passage(key: str, tier: str, published: dt.date | None, topics: list[str]) -> None:
        source, _ = await upsert_source(
            sess,
            f"https://{marker}-{key}.test/doc",
            checksum=f"sha256:{uuid.uuid4().hex}",
            source_tier=tier,
            title=f"{key} document",
            publication_date=published,
        )
        # Content labels are the labeller's to write (`P2-21`); set directly.
        source.topic_labels = topics
        await replace_chunks(
            sess, source.source_id, [ChunkWrite(text=f"{marker} {key} passage.", chunk_index=0)]
        )
        await sess.flush()
        w.chunk[key] = (
            await sess.scalars(select(Chunk.chunk_id).where(Chunk.source_id == source.source_id))
        ).one()
        sources.append(source.source_id)

    await passage("gov23", "government", dt.date(2023, 8, 1), ["walk"])
    await passage("gov22", "government", dt.date(2022, 3, 1), ["walk"])
    await passage("press24", "press", dt.date(2024, 2, 1), ["walk"])
    await passage("press24b", "press", dt.date(2024, 5, 1), [])
    await passage("peer21", "peer_reviewed", dt.date(2021, 4, 1), ["bus"])
    await passage("peer12", "peer_reviewed", dt.date(2012, 1, 1), [])
    await passage("undated", "informal", None, [])

    def entity(name: str, node_type: str = "concept", **kw) -> None:
        w.node[name] = Entity(canonical_name=f"{marker} {name}", node_type=node_type, **kw)

    entity("Focus", topic_labels=["walk"])
    entity("Alpha", "intervention", aliases=[f"{marker}-ALF"])
    entity("Bravo", "finding")
    entity("Charlie", "finding")
    entity("Delta", "finding")
    entity("Echo", "place")
    entity("Xray", topic_labels=["bus"])
    entity("Merged")
    entity("Hint1")
    entity("Hint2")
    entity("Hint3")
    # Two routes of the same length from S to T; the Q2 route has more support.
    entity("S")
    entity("Q1")
    entity("Q2")
    entity("T")
    # A chain three hops long, for the depth bound.
    for name in ("C0", "C1", "C2", "C3"):
        entity(name)
    sess.add_all(w.node.values())
    await sess.flush()
    w.node["Merged"].redirects_to = w.id("Alpha")

    def edge(key: str, a: str, b: str, chunks: list[str], **kw) -> None:
        w.edge[key] = Edge(
            from_node=w.id(a),
            to_node=w.id(b),
            relation_type=kw.pop("relation_type", "relates_to"),
            supporting_chunk_ids=[w.chunk[c] for c in chunks],
            **kw,
        )

    c = w.chunk
    edge("FA", "Focus", "Alpha", ["gov23", "gov22", "press24"], confidence=0.6)
    # Stored the other way round: neighbours are found in both directions.
    edge("BF", "Bravo", "Focus", ["peer21"], confidence=0.9, certainty="hedged")
    edge("FC", "Focus", "Charlie", ["press24", "gov23"], certainty="asserted")
    # Delta's evidence: a 2024 press item and a 2012 journal article. Neither
    # passage alone is "peer-reviewed from 2020 on".
    edge("FD", "Focus", "Delta", ["press24b", "peer12"], stance="opposes")
    edge("FE", "Focus", "Echo", ["undated"], topic_labels=["walk"])
    edge("FX", "Focus", "Xray", ["gov22"])
    edge("FM", "Focus", "Merged", ["gov23", "gov22", "press24", "peer21"])
    edge("AH1", "Alpha", "Hint1", ["gov23", "gov22"])
    edge("AH2", "Alpha", "Hint2", ["gov23"])
    edge("AH3", "Alpha", "Hint3", ["gov22"])
    edge("AC", "Alpha", "Charlie", ["gov22"])
    edge("SQ1", "S", "Q1", ["gov23"])
    edge("Q1T", "Q1", "T", ["gov23"])
    edge("SQ2", "S", "Q2", ["gov23", "gov22", "press24"])
    edge("Q2T", "Q2", "T", ["gov23", "gov22", "press24"])
    edge("C01", "C0", "C1", ["gov23"])
    edge("C12", "C1", "C2", ["gov23"])
    edge("C23", "C2", "C3", ["gov23"])
    sess.add_all(w.edge.values())
    await sess.flush()
    # §9: a contradiction names the other edge, on both sides.
    w.edge["FC"].contested_with = [w.edge["FD"].edge_id]
    w.edge["FD"].contested_with = [w.edge["FC"].edge_id]

    definition = AttributeDefinition(name=f"{marker}-density", scope="global")
    sess.add(definition)
    await sess.flush()
    sess.add(
        AttributeValue(
            entity_id=w.id("Bravo"),
            attribute_id=definition.attribute_id,
            value="high",
            confidence=0.7,
            supporting_chunk_ids=[c["gov23"]],
        )
    )
    sess.add(
        AttributeValue(
            entity_id=w.id("Focus"),
            attribute_id=definition.attribute_id,
            value="low",
            confidence=0.5,
            supporting_chunk_ids=[c["peer12"]],
        )
    )
    await sess.commit()

    yield w

    await sess.rollback()
    ids = [e.entity_id for e in w.node.values()]
    await sess.execute(delete(Edge).where(or_(Edge.from_node.in_(ids), Edge.to_node.in_(ids))))
    await sess.execute(delete(AttributeValue).where(AttributeValue.entity_id.in_(ids)))
    await sess.execute(
        delete(AttributeDefinition).where(AttributeDefinition.name.like(f"{marker}%"))
    )
    await sess.execute(delete(Entity).where(Entity.entity_id.in_(ids)))
    await sess.execute(delete(Source).where(Source.source_id.in_(sources)))
    await sess.commit()


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


async def hood(client, w: World, **params) -> dict:
    response = await client.get(
        f"/api/explore/graph/nodes/{w.id('Focus')}/neighbourhood", params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


def names(w: World, body: dict, role: str = "neighbour") -> list[str]:
    prefix = f"{w.marker} "
    return [n["canonical_name"].removeprefix(prefix) for n in body["nodes"] if n["role"] == role]


# --------------------------------------------------------------------------
# §12.2: depth 1, ranked, capped, and honest about the cap
# --------------------------------------------------------------------------


async def test_neighbours_are_ranked_by_support_in_both_directions(client, world) -> None:
    body = await hood(client, world)

    # Alpha 3 passages, Charlie 2, Delta 2, then the single-passage ones by
    # confidence (Bravo's 0.9 edge) and then by name. Bravo is stored as
    # Bravo -> Focus and must still be found.
    assert names(world, body) == ["Alpha", "Charlie", "Delta", "Bravo", "Echo", "Xray"]
    supports = [n["support"] for n in body["nodes"] if n["role"] == "neighbour"]
    assert supports == sorted(supports, reverse=True)


async def test_the_cap_is_applied_and_the_total_is_still_reported(client, world) -> None:
    body = await hood(client, world, limit=2)

    assert names(world, body) == ["Alpha", "Charlie"]
    assert (body["shown"], body["total"], body["unfiltered"], body["limit"]) == (2, 6, 6, 2)


async def test_a_limit_past_the_ceiling_is_refused(client, world) -> None:
    response = await client.get(
        f"/api/explore/graph/nodes/{world.id('Focus')}/neighbourhood",
        params={"limit": MAX_NEIGHBOURS + 1},
    )
    assert response.status_code == 422


async def test_a_merged_node_is_never_a_neighbour(client, world) -> None:
    # §5.5: the loser of a merge is a redirect. It has the best-supported edge
    # to the focus here, so ranking alone would put it first.
    body = await hood(client, world)

    assert "Merged" not in names(world, body)
    assert all(n["entity_id"] != world.id("Merged") for n in body["nodes"])


async def test_a_merged_focus_says_where_it_went(client, world) -> None:
    response = await client.get(f"/api/explore/graph/nodes/{world.id('Merged')}/neighbourhood")
    assert response.json()["redirects_to"] == world.id("Alpha")


@pytest.mark.parametrize(
    "path",
    [
        "/api/explore/graph/nodes/987654321/neighbourhood",
        "/api/explore/graph/nodes/987654321",
        "/api/explore/graph/path?source=987654321&target=987654322",
    ],
)
async def test_a_missing_node_is_a_404_that_names_it(client, path) -> None:
    response = await client.get(path)

    assert response.status_code == 404
    assert "987654321" in response.json()["detail"]


async def test_edges_are_classified_by_how_they_are_drawn(client, world) -> None:
    body = await hood(client, world)
    by_id = {e["edge_id"]: e for e in body["edges"]}

    assert by_id[world.edge["FA"].edge_id]["kind"] == "focus"
    # Alpha and Charlie are both shown, so the edge between them is drawn.
    assert by_id[world.edge["AC"].edge_id]["kind"] == "between"
    assert world.edge["FM"].edge_id not in by_id


async def test_hints_are_capped_per_neighbour_and_strongest_first(client, world) -> None:
    body = await hood(client, world)

    hints = names(world, body, "hint")
    # Two per neighbour: Hint1 (two passages) and the lower-id of the tied pair.
    assert hints == ["Hint1", "Hint2"]
    hint_edges = [e for e in body["edges"] if e["kind"] == "hint"]
    assert {e["edge_id"] for e in hint_edges} == {
        world.edge["AH1"].edge_id,
        world.edge["AH2"].edge_id,
    }


async def test_hints_are_never_already_on_screen(client, world) -> None:
    # Charlie is both a neighbour and one of Alpha's edges. Drawing it twice —
    # once labelled, once as an anonymous dot — would be two nodes for one
    # entity.
    body = await hood(client, world)

    ids = [n["entity_id"] for n in body["nodes"]]
    assert len(ids) == len(set(ids))


async def test_hover_card_counts_are_over_the_whole_graph(client, world) -> None:
    body = await hood(client, world)
    alpha = next(n for n in body["nodes"] if n["entity_id"] == world.id("Alpha"))

    # FA, AH1, AH2, AH3, AC — five edges, of which the hints cap drew fewer.
    assert alpha["degree"] == 5
    assert alpha["sources"] == 3  # gov23, gov22, press24
    assert alpha["newest"] == "2024-02-01"


async def test_contested_marks_the_neighbour_and_the_focus(client, world) -> None:
    body = await hood(client, world)
    flagged = {
        n["canonical_name"].removeprefix(f"{world.marker} ")
        for n in body["nodes"]
        if n["contested"]
    }

    assert flagged == {"Charlie", "Delta"}
    assert body["focus_contested"] is True
    fc = next(e for e in body["edges"] if e["edge_id"] == world.edge["FC"].edge_id)
    assert fc["contested_with"] == [world.edge["FD"].edge_id]


async def test_cross_topic_needs_known_topics_on_both_sides(client, world) -> None:
    body = await hood(client, world)
    crossing = [n for n in body["nodes"] if n["cross_topic"]]

    # Xray is labelled `bus` against the focus's `walk`. Every other neighbour
    # has no topics of its own, and unknown is not "different".
    assert [n["entity_id"] for n in crossing] == [world.id("Xray")]


def test_cross_topic_rule() -> None:
    assert is_cross_topic(["walk"], ["bus"])
    assert not is_cross_topic(["walk", "bus"], ["bus"])
    assert not is_cross_topic([], ["bus"])
    assert not is_cross_topic(["walk"], [])


# --------------------------------------------------------------------------
# §12.2's filters (P6-02) — mostly what must NOT come back
# --------------------------------------------------------------------------


async def test_tier_and_date_must_hold_for_the_same_passage(client, world) -> None:
    # Delta has a 2024 press passage and a 2012 peer-reviewed one. Asking for
    # peer-reviewed evidence from 2020 on must not keep it: no single passage
    # is both.
    body = await hood(client, world, tier="peer_reviewed", published_from="2020-01-01")

    assert names(world, body) == ["Bravo"]


async def test_an_undated_passage_fails_any_date_bound(client, world) -> None:
    body = await hood(client, world, published_to="2030-01-01")

    assert "Echo" not in names(world, body)


async def test_several_tiers_are_a_union(client, world) -> None:
    body = await hood(client, world, tier=["informal", "peer_reviewed"])

    assert sorted(names(world, body)) == ["Bravo", "Delta", "Echo"]


async def test_topic_matches_the_source_or_the_edge(client, world) -> None:
    body = await hood(client, world, topic="walk")

    # Alpha, Charlie, Xray through their sources; Echo only through the edge's
    # own label (its passage's source has none). Delta and Bravo have neither.
    assert sorted(names(world, body)) == ["Alpha", "Charlie", "Echo", "Xray"]


async def test_contested_only(client, world) -> None:
    body = await hood(client, world, contested_only="true")

    assert names(world, body) == ["Charlie", "Delta"]


async def test_attribute_filter(client, world) -> None:
    body = await hood(client, world, attribute=f"{world.marker}-density")

    assert names(world, body) == ["Bravo"]


async def test_filters_shrink_the_total_not_the_unfiltered_count(client, world) -> None:
    body = await hood(client, world, contested_only="true")

    assert (body["total"], body["unfiltered"]) == (2, 6)


async def test_facets_do_not_move_when_a_filter_is_applied(client, world) -> None:
    # A rail whose counts shrink as boxes are ticked hides the option that
    # would bring a neighbour back.
    plain = (await hood(client, world))["facets"]
    filtered = (await hood(client, world, tier="press"))["facets"]

    assert plain == filtered


async def test_facets_count_neighbours_per_value(client, world) -> None:
    facets = (await hood(client, world))["facets"]
    tiers = {f["value"]: f["count"] for f in facets["tiers"]}
    topics = {f["value"]: f["count"] for f in facets["topics"]}

    assert tiers == {"government": 3, "press": 3, "peer_reviewed": 2, "informal": 1}
    assert topics == {"walk": 4, "bus": 1}
    assert facets["contested"] == 2
    assert {f["value"]: f["count"] for f in facets["attributes"]} == {f"{world.marker}-density": 1}
    assert (facets["published_min"], facets["published_max"]) == ("2012-01-01", "2024-05-01")


async def test_every_tier_facet_is_a_tier_the_database_allows(client, world) -> None:
    # Drift: the rail renders these as checkboxes that are sent back as
    # filters, so a value outside the constraint would be a box that 422s.
    facets = (await hood(client, world))["facets"]

    assert {f["value"] for f in facets["tiers"]} <= set(SOURCE_TIER.enums)


@pytest.mark.parametrize(
    "params",
    [
        {"tier": "tabloid"},
        {"published_from": "2024-01-01", "published_to": "2020-01-01"},
        {"published_from": "not-a-date"},
        {"limit": 0},
    ],
)
async def test_malformed_filters_are_refused(client, world, params) -> None:
    response = await client.get(
        f"/api/explore/graph/nodes/{world.id('Focus')}/neighbourhood", params=params
    )
    assert response.status_code == 422


def test_filters_forbid_unknown_keys() -> None:
    # The filter set is saved with a view (P6-09). A misspelt key that was
    # silently dropped would save a view that does not filter.
    with pytest.raises(ValueError):
        GraphFilters.model_validate({"tier": ["press"]})


def test_no_filter_passes_every_edge_including_one_with_no_known_evidence() -> None:
    edge = Edge(from_node=1, to_node=2, relation_type="x", supporting_chunk_ids=[42])

    assert edge_passes(edge, {}, GraphFilters())
    assert not edge_passes(edge, {}, GraphFilters(tiers=["press"]))


# --------------------------------------------------------------------------
# The node panel
# --------------------------------------------------------------------------


async def panel(client, w: World, name: str) -> dict:
    response = await client.get(f"/api/explore/graph/nodes/{w.id(name)}")
    assert response.status_code == 200, response.text
    return response.json()


async def test_evidence_carries_the_citing_edges_certainty(client, world) -> None:
    body = await panel(client, world, "Focus")
    by_chunk = {e["hit"]["chunk_id"]: e for e in body["evidence"]}

    peer21 = by_chunk[world.chunk["peer21"]]
    assert (peer21["via"], peer21["certainty"], peer21["other_name"]) == (
        "edge",
        "hedged",
        f"{world.marker} Bravo",
    )


async def test_an_edge_citation_wins_over_an_attribute_citation(client, world) -> None:
    # peer12 is cited by the focus's own attribute and by the Delta edge. Only
    # the edge records stance, and dropping it for the bare attribute citation
    # would lose the measured part.
    body = await panel(client, world, "Focus")
    peer12 = next(e for e in body["evidence"] if e["hit"]["chunk_id"] == world.chunk["peer12"])

    assert peer12["via"] == "edge"
    assert peer12["stance"] == "opposes"


async def test_an_attribute_only_passage_claims_no_certainty(client, world) -> None:
    body = await panel(client, world, "Bravo")
    gov23 = next(e for e in body["evidence"] if e["hit"]["chunk_id"] == world.chunk["gov23"])

    assert gov23["via"] == "attribute"
    assert gov23["certainty"] is None


async def test_evidence_is_newest_first_and_undated_last(client, world) -> None:
    body = await panel(client, world, "Focus")
    dates = [e["hit"]["publication_date"] for e in body["evidence"]]

    dated = [d for d in dates if d is not None]
    assert dated == sorted(dated, reverse=True)
    assert dates[-1] is None
    assert body["evidence_total"] == len(body["evidence"])


async def test_contested_pairs_show_both_sides_by_name(client, world) -> None:
    body = await panel(client, world, "Focus")

    assert body["contested"] is True
    pairs = {(p["ours"]["edge_id"], p["theirs"]["edge_id"]) for p in body["contested_with"]}
    fc, fd = world.edge["FC"].edge_id, world.edge["FD"].edge_id
    assert pairs == {(fc, fd), (fd, fc)}
    theirs = next(p["theirs"] for p in body["contested_with"] if p["ours"]["edge_id"] == fc)
    assert theirs["to_name"] == f"{world.marker} Delta"
    assert theirs["evidence"]["chunk_id"] == world.chunk["press24b"]


async def test_a_contested_id_naming_a_missing_edge_is_dropped(client, world, session_for) -> None:
    sess = await session_for("rw")
    await sess.execute(
        Edge.__table__.update()
        .where(Edge.edge_id == world.edge["FX"].edge_id)
        .values(contested_with=[987654321])
    )
    await sess.commit()

    body = await panel(client, world, "Xray")

    # Still contested — the mark is on the edge — but no half-pair is drawn.
    assert body["contested"] is True
    assert body["contested_with"] == []


async def test_the_panel_has_no_embedding(client, world) -> None:
    assert "embedding" not in (await panel(client, world, "Focus"))["entity"]


# --------------------------------------------------------------------------
# Node search
# --------------------------------------------------------------------------


async def search(client, q: str, **params) -> list[dict]:
    response = await client.get("/api/explore/graph/search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()["matches"]


async def test_search_finds_by_alias_and_says_which(client, world) -> None:
    matches = await search(client, f"{world.marker}-alf")

    assert [m["entity_id"] for m in matches] == [world.id("Alpha")]
    assert matches[0]["matched_alias"] == f"{world.marker}-ALF"


async def test_search_puts_the_exact_name_first(client, world) -> None:
    matches = await search(client, f"{world.marker} S", limit=25)

    assert matches[0]["entity_id"] == world.id("S")
    assert matches[0]["matched_alias"] is None


async def test_search_skips_merged_nodes(client, world) -> None:
    assert await search(client, f"{world.marker} Merged") == []


async def test_search_treats_wildcards_as_text(client, world) -> None:
    # `%` must not match every entity in the graph.
    assert await search(client, f"{world.marker}%") == []


async def test_an_empty_query_is_refused(client) -> None:
    response = await client.get("/api/explore/graph/search", params={"q": ""})
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Path mode (P6-03)
# --------------------------------------------------------------------------


async def path(client, w: World, a: str, b: str, **params) -> dict:
    response = await client.get(
        "/api/explore/graph/path", params={"source": w.id(a), "target": w.id(b), **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_path_crosses_edges_in_either_direction(client, world) -> None:
    # Bravo -> Focus -> Alpha -> Hint1: the first hop is stored backwards.
    body = await path(client, world, "Bravo", "Hint1")

    assert body["found"] is True
    assert body["hops"] == 3
    assert [n["entity_id"] for n in body["nodes"]] == [
        world.id("Bravo"),
        world.id("Focus"),
        world.id("Alpha"),
        world.id("Hint1"),
    ]
    assert len(body["edges"]) == 3


async def test_of_two_equal_routes_the_better_supported_one_wins(client, world) -> None:
    body = await path(client, world, "S", "T")

    assert [n["entity_id"] for n in body["nodes"]] == [world.id("S"), world.id("Q2"), world.id("T")]


async def test_a_route_longer_than_the_bound_is_not_found(client, world) -> None:
    far = await path(client, world, "C0", "C3", max_depth=2)
    near = await path(client, world, "C0", "C3", max_depth=3)

    assert (far["found"], far["nodes"], far["hops"]) == (False, [], None)
    assert near["hops"] == 3


async def test_no_route_is_an_answer_not_an_error(client, world) -> None:
    body = await path(client, world, "C0", "S")

    assert body["found"] is False


async def test_a_path_never_passes_through_a_merged_node(client, world, session_for) -> None:
    # Give the merged node the only short route to Hint3's side; the search
    # must go round it rather than through it.
    sess = await session_for("rw")
    sess.add(
        Edge(
            from_node=world.id("Merged"),
            to_node=world.id("C0"),
            relation_type="relates_to",
            supporting_chunk_ids=[world.chunk["gov23"]],
        )
    )
    await sess.commit()

    body = await path(client, world, "Focus", "C0", max_depth=6)

    assert body["found"] is False


async def test_a_depth_past_the_ceiling_is_refused(client, world) -> None:
    response = await client.get(
        "/api/explore/graph/path",
        params={"source": world.id("S"), "target": world.id("T"), "max_depth": 99},
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# The contested list (task P6-10, §12.5's third entry point)
# --------------------------------------------------------------------------


async def contested(client, **params) -> dict:
    # The ceiling, so fixtures from other tests or the dev corpus cannot push
    # this test's pairs off the page.
    params.setdefault("limit", MAX_CONTESTED)
    response = await client.get("/api/explore/graph/contested", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def mine(w: World, body: dict) -> list[tuple[int, int]]:
    ids = {e.edge_id for e in w.edge.values()}
    return [
        (p["ours"]["edge_id"], p["theirs"]["edge_id"])
        for p in body["pairs"]
        if p["ours"]["edge_id"] in ids
    ]


async def test_each_disagreement_is_listed_once(client, world) -> None:
    """§9 marks both edges; reading every mark would list the pair twice."""
    body = await contested(client)

    fc, fd = world.edge["FC"].edge_id, world.edge["FD"].edge_id
    assert mine(world, body) == [(min(fc, fd), max(fc, fd))]
    pair = next(p for p in body["pairs"] if p["ours"]["edge_id"] == min(fc, fd))
    assert {pair["ours"]["to_name"], pair["theirs"]["to_name"]} == {
        f"{world.marker} Charlie",
        f"{world.marker} Delta",
    }
    assert pair["theirs"]["evidence"] is not None and pair["ours"]["evidence"] is not None


async def test_a_pair_marked_from_one_side_is_still_listed(client, world, session_for) -> None:
    """The other edge is fetched by id, not found by its own mark."""
    sess = await session_for("rw")
    await sess.execute(
        Edge.__table__.update()
        .where(Edge.edge_id == world.edge["FD"].edge_id)
        .values(contested_with=[])
    )
    await sess.commit()

    fc, fd = world.edge["FC"].edge_id, world.edge["FD"].edge_id
    assert mine(world, await contested(client)) == [(min(fc, fd), max(fc, fd))]


async def test_a_mark_naming_a_missing_edge_is_not_half_a_pair(
    client, world, session_for
) -> None:
    sess = await session_for("rw")
    await sess.execute(
        Edge.__table__.update()
        .where(Edge.edge_id == world.edge["FX"].edge_id)
        .values(contested_with=[987654321])
    )
    await sess.commit()

    body = await contested(client)

    assert world.edge["FX"].edge_id not in {
        s["edge_id"] for p in body["pairs"] for s in (p["ours"], p["theirs"])
    }


async def test_the_cap_is_applied_and_the_total_still_counts_every_pair(
    client, world, session_for
) -> None:
    sess = await session_for("rw")
    fa, ac = world.edge["FA"].edge_id, world.edge["AC"].edge_id
    await sess.execute(
        Edge.__table__.update().where(Edge.edge_id == fa).values(contested_with=[ac])
    )
    await sess.commit()

    body = await contested(client, limit=1)

    assert len(body["pairs"]) == 1
    assert body["total"] >= 2, "the total must count pairs the cap left out"


async def test_newest_disagreement_first(client, world, session_for) -> None:
    sess = await session_for("rw")
    fa, ac = world.edge["FA"].edge_id, world.edge["AC"].edge_id
    await sess.execute(
        Edge.__table__.update()
        .where(Edge.edge_id.in_([fa, ac]))
        .values(contested_with=[fa], created_at=dt.datetime(2000, 1, 1, tzinfo=dt.UTC))
    )
    await sess.execute(
        Edge.__table__.update().where(Edge.edge_id == fa).values(contested_with=[ac])
    )
    await sess.commit()

    order = mine(world, await contested(client))

    fc, fd = world.edge["FC"].edge_id, world.edge["FD"].edge_id
    assert order == [(min(fc, fd), max(fc, fd)), (min(fa, ac), max(fa, ac))]


@pytest.mark.parametrize("limit", [0, MAX_CONTESTED + 1])
async def test_a_limit_outside_the_bounds_is_refused(client, limit) -> None:
    response = await client.get("/api/explore/graph/contested", params={"limit": limit})
    assert response.status_code == 422
