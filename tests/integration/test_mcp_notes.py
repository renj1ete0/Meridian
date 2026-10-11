"""The operator's notes on the MCP surface (task `B-216`, docs/features/mcp.md#grants-design).

Notes are the operator's own thinking, and go to an `operator` token only. Every other
caller is the subject here: a note that leaks does so silently, through a node search, a
neighbour, a hint dot, an `annotates` edge's citation, or a guest's SQL. Each case has its
other half, the operator still seeing them, since a surface that hides notes from everyone
passes every refusal.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
import uuid
from typing import Any

import pytest
from sqlalchemy import delete, select, text

from api import auth
from api.mcp.server import build_mcp
from meridian_core import annotations, graphview
from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.grants import PROFILE_TOOLS
from meridian_core.models import AgentToken, Chunk, Edge, Entity, Grant, Source
from meridian_core.schemas.annotations import AnnotationCreate
from meridian_core.sources import upsert_source
from meridian_core.tokens import issue_token, resolve_token

pytestmark = pytest.mark.usefixtures("require_db")

SERVER = pathlib.Path(__file__).resolve().parents[2] / "services/api/api/mcp/server.py"


@pytest.fixture(autouse=True)
async def _dispose():
    yield
    from meridian_core.db import dispose_engines

    await dispose_engines()


@pytest.fixture
def marker() -> str:
    return f"nt{uuid.uuid4().hex[:10]}"


@pytest.fixture
async def graph(session_for, marker: str):
    """A subject linked to a neighbour, one note on each, and a passage the notes cite.

    Committed, because the tools read on their own connection, and removed afterwards.
    """
    sess = await session_for("rw")
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
        [
            ChunkWrite(
                text=f"{marker} a passage the operator cited in a private note.", chunk_index=0
            )
        ],
    )
    await sess.flush()
    chunk = (await sess.scalars(select(Chunk).where(Chunk.source_id == source.source_id))).one()

    subject = Entity(canonical_name=f"{marker} Subject", node_type="finding")
    neighbour = Entity(canonical_name=f"{marker} Neighbour", node_type="place")
    sess.add_all([subject, neighbour])
    await sess.flush()
    sess.add(
        Edge(
            from_node=subject.entity_id,
            to_node=neighbour.entity_id,
            relation_type="relates_to",
            supporting_chunk_ids=[],
        )
    )
    on_subject = await annotations.create(
        sess,
        AnnotationCreate(
            title=f"{marker} private thought",
            body="mine alone",
            about=[subject.entity_id],
            supporting_chunk_ids=[chunk.chunk_id],
        ),
    )
    on_neighbour = await annotations.create(
        sess, AnnotationCreate(title=f"{marker} second thought", about=[neighbour.entity_id])
    )
    await sess.commit()

    yield {
        "subject": subject.entity_id,
        "neighbour": neighbour.entity_id,
        "note": on_subject.entity_id,
        "hint_note": on_neighbour.entity_id,
        "chunk": chunk.chunk_id,
    }

    await sess.rollback()
    mine = select(Entity.entity_id).where(Entity.canonical_name.like(f"{marker}%"))
    await sess.execute(delete(Edge).where(Edge.from_node.in_(mine) | Edge.to_node.in_(mine)))
    await sess.execute(delete(Entity).where(Entity.canonical_name.like(f"{marker}%")))
    await sess.execute(delete(Source).where(Source.source_id == source.source_id))
    await sess.commit()


def caller(monkeypatch, *, notes: bool | None) -> None:
    """Every read tool in scope; ``notes`` as a verified token carries it, None for none."""
    monkeypatch.setenv("MERIDIAN_MCP_ALLOW_ANONYMOUS", "")

    class _Token:
        scopes = sorted(PROFILE_TOOLS["operator"])

    token = _Token()
    if notes is not None:
        token.notes = notes
    monkeypatch.setattr(auth, "get_access_token", lambda: token)


async def call(name: str, **arguments: Any) -> Any:
    result = await build_mcp(version="test").call_tool(name, arguments)
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def mentions(body: Any, entity_id: int) -> bool:
    """Whether an entity id appears anywhere in a tool's answer, as an id field."""
    found = False

    def walk(value: Any) -> None:
        nonlocal found
        if isinstance(value, dict):
            for key, inner in value.items():
                if key in {"entity_id", "other_entity_id", "from_node", "to_node"}:
                    found = found or inner == entity_id
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)

    walk(body)
    return found


# --------------------------------------------------------------------------
# The tools
# --------------------------------------------------------------------------


@pytest.mark.parametrize("notes", [False, None])
async def test_a_node_search_does_not_find_a_note(monkeypatch, graph, marker, notes) -> None:
    caller(monkeypatch, notes=notes)
    found = await call("find_nodes", query=marker)
    ids = {m["entity_id"] for m in found["matches"]}
    assert graph["subject"] in ids
    assert graph["note"] not in ids and graph["hint_note"] not in ids


async def test_the_operator_still_finds_notes(monkeypatch, graph, marker) -> None:
    caller(monkeypatch, notes=True)
    found = await call("find_nodes", query=marker)
    assert graph["note"] in {m["entity_id"] for m in found["matches"]}


async def test_a_node_carries_no_note_anywhere_in_it(monkeypatch, graph, marker) -> None:
    """The panel's notes, the note as a neighbour, a note on a neighbour as a hint dot,
    and the `annotates` edge's citation as evidence: four ways in, one assertion each."""
    caller(monkeypatch, notes=False)
    body = await call("get_node", entity_id=graph["subject"])

    assert body["node"]["annotations"] == []
    assert not mentions(body, graph["note"]), "the note reached the answer"
    assert not mentions(body, graph["hint_note"]), "a note on a neighbour reached the answer"
    assert graph["chunk"] not in {e["hit"]["chunk_id"] for e in body["node"]["evidence"]}
    assert marker + " private" not in json.dumps(body)
    # The rest of the node is still there.
    assert mentions(body["neighbourhood"], graph["neighbour"])


async def test_the_operator_still_sees_the_notes_on_a_node(monkeypatch, graph) -> None:
    caller(monkeypatch, notes=True)
    body = await call("get_node", entity_id=graph["subject"])
    assert [a["entity_id"] for a in body["node"]["annotations"]] == [graph["note"]]
    assert mentions(body["neighbourhood"], graph["note"])
    assert mentions(body["neighbourhood"], graph["hint_note"])


async def test_a_note_asked_for_by_id_is_not_found(monkeypatch, graph) -> None:
    """Not found, rather than refused: a refusal would confirm the id is a note."""
    caller(monkeypatch, notes=False)
    body = await call("get_node", entity_id=graph["note"])
    assert body == {"error": f"No entity {graph['note']}."}


def test_every_graph_view_that_can_hold_a_note_is_told_whether_to() -> None:
    """Completeness over the server's source: a graphview function taking `notes` and
    called from the MCP server without it would default to showing them."""
    tree = ast.parse(SERVER.read_text())
    takes_notes = {
        name
        for name, fn in inspect.getmembers(graphview, inspect.isfunction)
        if "notes" in inspect.signature(fn).parameters
    }
    assert takes_notes >= {"search_nodes", "node_detail", "neighbourhood"}
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "graphview"
        and node.func.attr in takes_notes
    ]
    assert calls, "the server calls no graph view that holds notes; this test checks nothing"
    for found in calls:
        assert "notes" in {k.arg for k in found.keywords}, (
            f"graphview.{found.func.attr} on line {found.lineno} is not told whether to show notes"
        )


# --------------------------------------------------------------------------
# Which token sees them
# --------------------------------------------------------------------------


@pytest.fixture
async def tokens(session_for, marker):
    sess = await session_for("rw")
    yield sess
    await sess.rollback()
    await sess.execute(delete(AgentToken).where(AgentToken.agent_id.like(f"{marker}%")))
    await sess.execute(delete(Grant).where(Grant.subject.like(f"{marker}%")))
    await sess.commit()


@pytest.mark.parametrize(
    ("profile", "sees"),
    [("operator", True), ("analyst", False), ("reader", False), (None, False)],
)
async def test_only_an_operator_token_sees_notes(tokens, marker, profile, sees) -> None:
    """`analyst` carries exactly `operator`'s tools, so the tools cannot be what decides."""
    secret, _ = await issue_token(
        tokens,
        agent_id=f"{marker}-{profile}",
        allowed_tools=sorted(PROFILE_TOOLS["operator"]),
        profile=profile,
    )
    assert (await resolve_token(tokens, secret)).notes is sees


@pytest.mark.parametrize(("grant_profile", "sees"), [("operator", True), ("reader", False)])
async def test_a_token_sees_no_more_than_its_grant(tokens, marker, grant_profile, sees) -> None:
    grant = Grant(subject=f"{marker}@example.test", subject_kind="service", profile=grant_profile)
    tokens.add(grant)
    await tokens.flush()
    secret, row = await issue_token(
        tokens,
        agent_id=f"{marker}-granted",
        allowed_tools=sorted(PROFILE_TOOLS["operator"]),
        profile="operator",
    )
    row.grant_id = grant.grant_id
    await tokens.flush()
    assert (await resolve_token(tokens, secret)).notes is sees


async def test_the_verifier_hands_the_answer_to_the_tools(tokens, marker) -> None:
    secret, _ = await issue_token(
        tokens, agent_id=f"{marker}-v", allowed_tools=["get_node"], profile="operator"
    )
    await tokens.commit()
    verified = await auth.MeridianTokenVerifier().verify_token(secret)
    assert verified.notes is True


def test_a_token_this_server_did_not_make_sees_none(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_MCP_ALLOW_ANONYMOUS", "")
    monkeypatch.setattr(auth, "get_access_token", lambda: object())
    assert auth.sees_notes() is False


@pytest.mark.parametrize(("anonymous", "sees"), [("true", True), ("", False)])
def test_without_a_token_only_the_loopback_operator_sees_notes(
    monkeypatch, anonymous, sees
) -> None:
    monkeypatch.setenv("MERIDIAN_MCP_ALLOW_ANONYMOUS", anonymous)
    monkeypatch.setattr(auth, "get_access_token", lambda: None)
    assert auth.sees_notes() is sees


# --------------------------------------------------------------------------
# Read-only SQL
# --------------------------------------------------------------------------


async def test_a_guest_reads_no_note_and_no_edge_attaching_one(session_for, graph) -> None:
    """`run_readonly_query` runs as the guest role, under every profile."""
    sess = await session_for("owner")
    await sess.execute(text("SET LOCAL ROLE meridian_guest"))
    seen = set(
        await sess.scalars(
            text("SELECT entity_id FROM entities WHERE entity_id = ANY(:ids)"),
            {"ids": [graph["subject"], graph["note"], graph["hint_note"]]},
        )
    )
    attaching = await sess.scalar(
        text("SELECT count(*) FROM edges WHERE from_node = :note"), {"note": graph["note"]}
    )
    assert seen == {graph["subject"]}
    assert attaching == 0
    assert not await sess.scalar(text("SELECT bool_or(is_annotation) FROM entities"))


@pytest.mark.parametrize("role", ["ro", "rw"])
async def test_the_operators_own_roles_still_read_notes(session_for, graph, role) -> None:
    sess = await session_for(role)
    assert await sess.get(Entity, graph["note"]) is not None
    assert await sess.scalar(select(Edge.edge_id).where(Edge.from_node == graph["note"]).limit(1))
