"""Annotation as first-class nodes (task P6-05, spec §12.5, §12.6, §2).

> "**Annotation as first-class nodes** — my own notes and edges, tagged as mine.
> Over months this becomes the highest-quality layer in the system and the one
> that actually reflects my thinking. Build the affordance early or it won't get
> used."

Written before the graph exists, like `P6-04`, and for a stronger reason: an
annotation is the one node a person writes by hand, so it needs `entities` and
nothing that fills them. Nothing here waits on `P4-01`.

Four properties carry the feature, and each is a rule the database or the spec
already states rather than a preference.

**"Tagged as mine" has to mean something.** The layer is worth having because a
reader can tell their own thinking from the corpus's, so authorship is set by
the server and cannot be claimed by a caller (§11.8: never trust the structure
in a request). A note that *says* a human wrote it, on a surface where anything
could say that, is not a distinguishable layer.

**A human is not on the agent registry's scale.** §11.12's `quality_tier` is an
ordinal over models — local small to hosted frontier — and a note carrying one
would be ranked against model output on an axis it does not belong to. It stays
null, and so does `model`.

**An annotation edge is the one edge with no chunk behind it.** §2 principle 3
requires every edge to name what justified it; for a note the justification is
the author, which provenance records. What is *not* allowed is a citation that
goes nowhere, so a chunk id the corpus does not have is refused rather than
stored.

**The derived graph is not editable from here.** This surface writes the
reader's own nodes. Pointing it at a corpus-derived entity has to fail, or
"annotation" becomes a hand-editing path into material §2.4 says is re-derived
from source.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from api.main import create_app
from meridian_core.annotations import ANNOTATES, HUMAN, MAX_ABOUT
from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.db import dispose_engines
from meridian_core.models import Chunk, Edge, Entity, Source
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def marker() -> str:
    return f"an{uuid.uuid4().hex[:10]}"


@pytest.fixture
def open_admin(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "true")


@pytest.fixture
async def client():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def corpus(session_for, marker: str):
    """A node somebody might annotate, and a chunk they might cite while doing it.

    Committed, because the app reads on its own connection; cleaned up
    afterwards, because the dev database is a real corpus somebody else is
    using. Annotations created by a test are deleted by title prefix, so a test
    that writes one does not have to remember to.
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
        [ChunkWrite(text=f"{marker} a passage worth arguing with.", chunk_index=0)],
    )
    await sess.flush()
    chunk = (await sess.scalars(select(Chunk).where(Chunk.source_id == source.source_id))).one()

    subject = Entity(canonical_name=f"{marker} Subject", node_type="finding")
    other = Entity(canonical_name=f"{marker} Other", node_type="place")
    sess.add_all([subject, other])
    await sess.commit()

    yield sess, subject, other, chunk

    await sess.rollback()
    mine = select(Entity.entity_id).where(Entity.canonical_name.like(f"{marker}%"))
    await sess.execute(delete(Edge).where(Edge.from_node.in_(mine) | Edge.to_node.in_(mine)))
    await sess.execute(delete(Entity).where(Entity.canonical_name.like(f"{marker}%")))
    await sess.execute(delete(Source).where(Source.source_id == source.source_id))
    await sess.commit()


async def note(client, marker: str, **body) -> httpx.Response:
    return await client.post("/api/admin/annotations", json={"title": f"{marker} note", **body})


async def edges_from(session_for, entity_id: int) -> list[Edge]:
    sess = await session_for("rw")
    await sess.rollback()
    return list(await sess.scalars(select(Edge).where(Edge.from_node == entity_id)))


# --------------------------------------------------------------------------
# Writing a note (§12.5)
# --------------------------------------------------------------------------


async def test_a_note_is_a_node(client, open_admin, corpus, marker) -> None:
    # "Annotation as first-class nodes" — a row in `entities`, not a side table.
    # It is what lets a note be reached by the same traversal, path mode and
    # canvas filters everything else is (§12.1, §12.3).
    _, subject, _, _ = corpus

    body = (await note(client, marker, about=[subject.entity_id])).json()

    assert body["entity_id"] > 0
    assert body["title"] == f"{marker} note"


async def test_a_note_is_tagged_as_the_readers_own(client, open_admin, corpus, marker) -> None:
    # §12.5's "tagged as mine", and the answer comes back with the note rather
    # than having to be looked up: a client that has to fetch the node again to
    # find out who wrote it will render the layer undifferentiated.
    _, subject, _, _ = corpus

    written = (await note(client, marker, about=[subject.entity_id])).json()

    assert written["produced_by"] == HUMAN


async def test_a_note_claims_no_model_and_no_quality_tier(
    client, open_admin, corpus, marker, session_for
) -> None:
    # §11.12's tier is an ordinal over the agent registry — local small to
    # hosted frontier. A note carrying one would be compared against model
    # output on a scale it is not on, and "quality tier only moves up" would
    # then be a rule about a person.
    _, subject, _, _ = corpus
    written = (await note(client, marker, about=[subject.entity_id])).json()

    sess = await session_for("rw")
    await sess.rollback()
    row = await sess.get(Entity, written["entity_id"])

    assert row.model is None
    assert row.quality_tier is None
    assert row.is_annotation is True
    assert row.node_type == "annotation"


async def test_authorship_cannot_be_claimed_by_the_caller(
    client, open_admin, corpus, marker
) -> None:
    # The layer is only worth having because a reader can tell it apart from the
    # corpus. If a request could set `produced_by`, anything could call itself
    # the reader's own thinking — including, later, a model with a write tool
    # (`P4-04`). Refused at the boundary rather than overwritten quietly.
    _, subject, _, _ = corpus

    response = await note(
        client, marker, about=[subject.entity_id], produced_by="some-agent", quality_tier=4
    )

    assert response.status_code == 422


async def test_a_note_attaches_to_what_it_is_about(
    client, open_admin, corpus, marker, session_for
) -> None:
    # "my own notes *and edges*". The attachment is an edge like any other, so
    # it is reachable by the same traversal as the rest of the graph.
    _, subject, _, _ = corpus
    written = (await note(client, marker, about=[subject.entity_id])).json()

    edges = await edges_from(session_for, written["entity_id"])

    assert [(e.to_node, e.relation_type) for e in edges] == [(subject.entity_id, ANNOTATES)]


async def test_a_note_can_be_about_several_nodes(
    client, open_admin, corpus, marker, session_for
) -> None:
    # The most useful note is often the one that spans two nodes — "these
    # disagree", "this is the same programme under another name".
    _, subject, other, _ = corpus
    written = (await note(client, marker, about=[subject.entity_id, other.entity_id])).json()

    edges = await edges_from(session_for, written["entity_id"])

    assert {e.to_node for e in edges} == {subject.entity_id, other.entity_id}


async def test_a_note_can_stand_on_its_own(client, open_admin, corpus, marker) -> None:
    # A thought that has not found its node yet is still worth keeping. Forcing
    # a target would mean the affordance is unavailable exactly when somebody is
    # reading something the graph does not cover — which is when it is most
    # worth having.
    response = await note(client, marker)

    assert response.status_code == 201
    assert response.json()["about"] == []


async def test_a_note_about_a_node_that_does_not_exist_is_refused(
    client, open_admin, corpus, marker, session_for
) -> None:
    # And writes nothing. A partly-applied write here is an annotation with a
    # dangling edge, which is the shape that survives review because the note
    # itself looks fine.
    response = await note(client, marker, about=[99999999])

    assert response.status_code == 404
    sess = await session_for("rw")
    await sess.rollback()
    assert (
        await sess.scalars(select(Entity).where(Entity.canonical_name == f"{marker} note"))
    ).all() == []


async def test_more_targets_than_the_cap_are_refused(client, open_admin, corpus, marker) -> None:
    # A note about fifty nodes is a tag, not a note, and the cap is what keeps
    # one request from writing fifty edges.
    _, subject, _, _ = corpus

    response = await note(client, marker, about=[subject.entity_id] * (MAX_ABOUT + 1))

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Citations (§2 principle 3)
# --------------------------------------------------------------------------


async def test_a_cited_passage_rides_on_the_edge(
    client, open_admin, corpus, marker, session_for
) -> None:
    # §12.5 puts annotation inside reading, so the passage in front of the
    # reader is the thing the note is usually about. Stored where every other
    # edge stores its evidence rather than in the note's prose.
    _, subject, _, chunk = corpus
    written = (
        await note(client, marker, about=[subject.entity_id], supporting_chunk_ids=[chunk.chunk_id])
    ).json()

    edges = await edges_from(session_for, written["entity_id"])

    assert edges[0].supporting_chunk_ids == [chunk.chunk_id]


async def test_a_citation_that_resolves_to_nothing_is_refused(
    client, open_admin, corpus, marker
) -> None:
    # The one thing this corpus exists to prevent. A chunk id nobody can follow
    # is worse on an annotation than anywhere else, because the annotation layer
    # is the part a reader trusts without re-checking.
    _, subject, _, _ = corpus

    response = await note(
        client, marker, about=[subject.entity_id], supporting_chunk_ids=[99999999]
    )

    assert response.status_code == 422


async def test_a_note_with_no_citation_is_still_an_edge(
    client, open_admin, corpus, marker, session_for
) -> None:
    # The one edge in the system with an empty `supporting_chunk_ids`, and the
    # reason it is allowed: §2 principle 3 asks what justified the edge, and for
    # a note that is the author — recorded in provenance, which is why
    # authorship being unforgeable above is what makes this safe.
    _, subject, _, _ = corpus
    written = (await note(client, marker, about=[subject.entity_id])).json()

    edges = await edges_from(session_for, written["entity_id"])

    assert edges[0].supporting_chunk_ids == []
    assert edges[0].produced_by == HUMAN


# --------------------------------------------------------------------------
# Titles are not handles
# --------------------------------------------------------------------------


async def test_two_notes_may_share_a_title(client, open_admin, corpus, marker) -> None:
    # `entities` is unique on (canonical_name, node_type, jurisdiction), which
    # for annotations never bites: jurisdiction is null and Postgres treats
    # nulls in a unique key as distinct. That is the behaviour a notebook wants
    # — two notes called "check this" are ordinary — and it is worth asserting
    # so that tightening the constraint later fails here rather than in a
    # reader's way.
    _, subject, _, _ = corpus

    first = await note(client, marker, about=[subject.entity_id])
    second = await note(client, marker, about=[subject.entity_id])

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["entity_id"] != second.json()["entity_id"]


# --------------------------------------------------------------------------
# Rewriting a note (§12.5)
# --------------------------------------------------------------------------


async def test_a_note_can_be_rewritten(client, open_admin, corpus, marker) -> None:
    _, subject, _, _ = corpus
    written = (await note(client, marker, about=[subject.entity_id], body="first")).json()

    edited = await client.patch(
        f"/api/admin/annotations/{written['entity_id']}", json={"body": "second"}
    )

    assert edited.json()["body"] == "second"


async def test_rewriting_moves_the_authored_time_and_not_the_created_time(
    client, open_admin, corpus, marker
) -> None:
    # Two different questions — when the note appeared, and when its text was
    # last the author's. A list ordered by the first shows a note rewritten
    # today in the position it had in March.
    _, subject, _, _ = corpus
    written = (await note(client, marker, about=[subject.entity_id], body="first")).json()

    edited = (
        await client.patch(
            f"/api/admin/annotations/{written['entity_id']}", json={"body": "second"}
        )
    ).json()

    assert edited["created_at"] == written["created_at"]
    assert edited["produced_at"] > written["produced_at"]


async def test_rewriting_can_re_point_what_a_note_is_about(
    client, open_admin, corpus, marker, session_for
) -> None:
    # Re-reading changes what a note is about. The old edge goes, rather than
    # accumulating every node the note was ever pointed at.
    _, subject, other, _ = corpus
    written = (await note(client, marker, about=[subject.entity_id])).json()

    await client.patch(
        f"/api/admin/annotations/{written['entity_id']}", json={"about": [other.entity_id]}
    )

    edges = await edges_from(session_for, written["entity_id"])
    assert [e.to_node for e in edges] == [other.entity_id]


async def test_a_corpus_node_cannot_be_rewritten_through_this_surface(
    client, open_admin, corpus
) -> None:
    # The important refusal. §2.4 re-derives the graph from source chunks, and a
    # hand-edit that survives into a derived node is a change nothing can
    # re-derive or explain. This surface writes the reader's own nodes only.
    _, subject, _, _ = corpus

    response = await client.patch(
        f"/api/admin/annotations/{subject.entity_id}", json={"body": "rewritten by hand"}
    )

    assert response.status_code == 404


async def test_rewriting_cannot_reassign_authorship(client, open_admin, corpus, marker) -> None:
    _, subject, _, _ = corpus
    written = (await note(client, marker, about=[subject.entity_id])).json()

    response = await client.patch(
        f"/api/admin/annotations/{written['entity_id']}", json={"produced_by": "some-agent"}
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Reading them back (§12.5)
# --------------------------------------------------------------------------


async def test_the_node_panel_carries_the_readers_own_notes(
    client, open_admin, corpus, marker
) -> None:
    # §12.5's node detail panel ends with "own annotations", and this is the
    # only part of that panel a person wrote themselves.
    _, subject, _, _ = corpus
    written = (await note(client, marker, about=[subject.entity_id], body="mine")).json()

    panel = (await client.get(f"/api/explore/nodes/{subject.entity_id}")).json()

    assert [a["entity_id"] for a in panel["annotations"]] == [written["entity_id"]]
    assert panel["annotations"][0]["body"] == "mine"


async def test_a_note_names_what_it_is_about_rather_than_its_ids(
    client, open_admin, corpus, marker
) -> None:
    # The same rule `P6-04` applied to attribute names: a panel showing
    # `entity_id: 412` asks the reader to resolve a foreign key by hand.
    _, subject, other, _ = corpus
    await note(client, marker, about=[subject.entity_id, other.entity_id])

    listed = (
        await client.get("/api/explore/annotations", params={"about": subject.entity_id})
    ).json()

    names = {t["canonical_name"] for t in listed["annotations"][0]["about"]}
    assert names == {f"{marker} Subject", f"{marker} Other"}


async def test_the_list_narrows_to_one_node(client, open_admin, corpus, marker) -> None:
    _, subject, other, _ = corpus
    mine = (await note(client, marker, about=[subject.entity_id])).json()
    await note(client, f"{marker}b", about=[other.entity_id])

    listed = (
        await client.get("/api/explore/annotations", params={"about": subject.entity_id})
    ).json()

    assert [a["entity_id"] for a in listed["annotations"]] == [mine["entity_id"]]


async def test_the_list_is_most_recently_written_first(client, open_admin, corpus, marker) -> None:
    # Ordered by when the text was last the author's, not when the row appeared,
    # so a note rewritten today comes back to the top.
    _, subject, _, _ = corpus
    first = (await note(client, marker, about=[subject.entity_id])).json()
    second = (await note(client, f"{marker}b", about=[subject.entity_id])).json()
    await client.patch(f"/api/admin/annotations/{first['entity_id']}", json={"body": "again"})

    listed = (
        await client.get("/api/explore/annotations", params={"about": subject.entity_id})
    ).json()

    assert [a["entity_id"] for a in listed["annotations"]] == [
        first["entity_id"],
        second["entity_id"],
    ]


async def test_an_annotation_edge_is_not_counted_as_contested(
    client, open_admin, corpus, marker
) -> None:
    # §9's contested count is two sources disagreeing. A note is one reader
    # disagreeing, which is a different claim, and folding it into that count
    # would make the panel's warning mean two things.
    _, subject, _, _ = corpus
    await note(client, marker, about=[subject.entity_id])

    panel = (await client.get(f"/api/explore/nodes/{subject.entity_id}")).json()

    assert panel["contested_edges"] == 0


# --------------------------------------------------------------------------
# Export (§12.5: "Markdown (notes) — avoid trapping material in a bespoke store")
# --------------------------------------------------------------------------


async def test_notes_export_as_markdown(client, open_admin, corpus, marker) -> None:
    _, subject, _, _ = corpus
    await note(client, marker, about=[subject.entity_id], body="the thought")

    exported = await client.get(
        "/api/explore/export/annotations", params={"about": subject.entity_id}
    )

    assert f"{marker} note" in exported.text
    assert "the thought" in exported.text
    assert f"{marker} Subject" in exported.text


# --------------------------------------------------------------------------
# The write gate (§12.6)
# --------------------------------------------------------------------------


async def test_writing_a_note_is_refused_on_an_unidentified_instance(
    client, corpus, marker, monkeypatch
) -> None:
    # Same gate as every other write. An annotation surface open to the internet
    # is a way to put text into the corpus that reads as the owner's own
    # thinking, which is the worst thing on this system to be able to forge.
    monkeypatch.delenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)

    response = await note(client, marker)

    assert response.status_code == 503


async def test_reading_notes_needs_no_gate(client, corpus, monkeypatch) -> None:
    # Reading stays on `/api/explore` and the read-only role, so a shared
    # instance (`P3-06`) can show the owner's notes without offering a way to
    # add to them.
    monkeypatch.delenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", raising=False)

    assert (await client.get("/api/explore/annotations")).status_code == 200
