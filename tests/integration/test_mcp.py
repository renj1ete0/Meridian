"""The MCP read surface (tasks P3-01, P3-02; spec §11.1, §11.6).

Against a real Postgres, because every tool here ends in a query and the thing
worth testing is what an external model actually receives — not that a function
was registered.

**Most of these are about what the tools *say*, not what they return.** That is
unusual for this suite and it is the point. The consumer is a language model
with no other source of truth about this corpus: it will act on the wording it
is given, summarise away anything it was not told to care about, and report its
conclusions to someone who cannot check them. Three of the mistakes it would
make by default are ones the whole system exists to prevent — treating an empty
result as evidence of absence, reading `source_tier` as a credibility score, and
asserting a claim without its citation — so the guidance against them is
load-bearing and is tested as such.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text

from api.mcp.server import HYBRID, INSTRUCTIONS, LEXICAL_ONLY, build_mcp

pytestmark = pytest.mark.usefixtures("require_db")

#: §11.6's write group. None of it may be reachable from the read surface —
#: these belong to the orchestrator scope (§11.4) and arrive with the graph.
WRITE_TOOLS = {"add_edge", "tag_entity", "enqueue_seed", "advance_mark", "propose_attribute"}


@pytest.fixture
def mcp():
    return build_mcp(version="test")


@pytest.fixture(autouse=True)
async def _dispose_engines():
    """Drop the engine pool between tests.

    The tools open their own sessions through `session_ro()` rather than taking
    one from a fixture — they are called by an MCP client, not by a request with
    dependency injection — and `meridian_core.db` caches engines per process.
    pytest-asyncio gives each test its own event loop, so the second test to run
    inherits a pool whose connections belong to a loop that has closed, and
    fails with "attached to a different loop" several frames from the cause.
    """
    yield
    from meridian_core.db import dispose_engines

    await dispose_engines()


async def call(mcp, name: str, **arguments: Any) -> Any:
    result = await mcp.call_tool(name, arguments)
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


# --------------------------------------------------------------------------
# What the model is told before it uses anything
# --------------------------------------------------------------------------


def test_the_instructions_warn_that_no_results_is_not_absence(mcp) -> None:
    """The failure this surface makes easy and expensive.

    A model that searches, finds nothing, and reports "the corpus has nothing on
    this" has produced a confident false statement about evidence — to someone
    who trusts it precisely because it cites sources. A crash would be noticed;
    this looks like an answer.
    """
    assert "NO RESULTS DOES NOT MEAN NO EVIDENCE" in INSTRUCTIONS
    assert "alternative wordings" in INSTRUCTIONS


def test_the_instructions_refuse_to_let_tier_become_a_score(mcp) -> None:
    """§8 extracts structure — funder, stance, hedging — and deliberately scores
    nothing. An external model shown `source_tier: informal` will rank it below
    `peer_reviewed` unless told not to, which makes the interface assert a
    credibility judgement the system refuses to compute.

    The design system says the same thing about colour (§2: "colour must not
    imply a verdict"). This is the same rule, for the same reason, aimed at the
    consumer that will actually make the mistake.
    """
    assert "NOT A CREDIBILITY SCORE" in INSTRUCTIONS
    # Matched in pieces: the sentence wraps, and asserting across a line
    # break would make this fail on a reflow rather than on a meaning change.
    assert "do not convert it into a ranking" in INSTRUCTIONS
    assert "the only record that exists" in INSTRUCTIONS


def test_the_instructions_require_citation(mcp) -> None:
    """§2 principle 3, stated to the only party that can honour it."""
    assert "CITE EVERYTHING" in INSTRUCTIONS


def test_the_server_hands_the_instructions_over(mcp) -> None:
    """They are useless sitting in a constant. A client reads them once, at
    initialize, so they have to be attached to the server rather than to the
    documentation."""
    assert mcp.instructions == INSTRUCTIONS


# --------------------------------------------------------------------------
# No write tool may be reachable here
# --------------------------------------------------------------------------


async def test_no_write_tool_is_registered(mcp) -> None:
    """Not disabled — *absent*. §11.6 splits read from write and §11.4 puts
    write scope on the orchestrator's own profile only; a write tool present and
    guarded is one refactor away from being present and unguarded.
    """
    registered = {tool.name for tool in await mcp.list_tools()}
    assert not (registered & WRITE_TOOLS), (
        f"write tools on the read surface: {registered & WRITE_TOOLS}"
    )


async def test_every_registered_tool_describes_itself(mcp) -> None:
    """A tool with no description is one the model will use wrongly or not at
    all — the description is the entire interface it gets."""
    for tool in await mcp.list_tools():
        assert tool.description, f"{tool.name} has no description"


# --------------------------------------------------------------------------
# What every result carries
# --------------------------------------------------------------------------


async def test_a_search_result_is_citable(mcp) -> None:
    """Text without provenance makes this a RAG endpoint over someone's files,
    which the README is explicit it is not."""
    body = await call(mcp, "search_chunks", query="transport", limit=1)
    if not body["results"]:
        pytest.skip("dev corpus has no match for the probe term")

    hit = body["results"][0]
    for field in ("url", "title", "source_tier", "page_or_offset", "chunk_id", "source_id"):
        assert field in hit, f"a result without {field} cannot be cited"


async def test_the_retrieval_mode_rides_on_every_search(mcp) -> None:
    """In the payload, not only in the server instructions.

    A client reads instructions once at connect and then summarises individual
    tool calls. The warning has to be attached to the thing being summarised or
    it does not survive the summary.
    """
    body = await call(mcp, "search_chunks", query="transport")
    assert body["retrieval"] in (LEXICAL_ONLY, HYBRID)
    assert body["arms"]


async def test_an_empty_search_still_carries_the_caveat(mcp) -> None:
    """The case where it matters most, and the one an implementation naturally
    skips: zero results plus no explanation is exactly the input that produces
    "your corpus has nothing on this".
    """
    body = await call(mcp, "search_chunks", query="qzx-no-such-term-anywhere")

    assert body["returned"] == 0
    assert body["retrieval"], "an empty result set arrived with no explanation"


def test_the_degraded_wording_tells_the_model_what_to_do(mcp) -> None:
    """Not just that retrieval was partial — what to do about it.

    A client told only that a flag is true will not think to try synonyms.
    Told to, it will, and that agentic retry is what makes lexical-only
    retrieval usable at all while `P2-17` does not exist.
    """
    assert "retry with alternative" in LEXICAL_ONLY
    assert "Do not conclude the corpus lacks this topic" in LEXICAL_ONLY


# --------------------------------------------------------------------------
# The mark walks forward exactly once
# --------------------------------------------------------------------------


async def test_list_new_since_resumes_without_overlap_or_gap(mcp) -> None:
    """§11.1a's entry point: a synthesis session walks what is new rather than
    searching for it, so this path needs no query and no embedder at all.

    Ids are monotonic, so "everything after N" cannot skip a row that arrived
    mid-read and cannot return one twice — but only if the returned mark is the
    last id seen rather than an offset.
    """
    first = await call(mcp, "list_new_since", mark=0, limit=2)
    if first["returned"] < 2:
        pytest.skip("dev corpus too small to page")

    second = await call(mcp, "list_new_since", mark=first["next_mark"], limit=2)

    ids_first = {p["chunk_id"] for p in first["passages"]}
    ids_second = {p["chunk_id"] for p in second["passages"]}
    assert not (ids_first & ids_second), "the same passage was handed out twice"
    assert all(i > first["next_mark"] for i in ids_second), "the mark went backwards"


async def test_an_exhausted_mark_does_not_rewind(mcp) -> None:
    """A caller polling for new work must not be sent back to the beginning when
    there is none — that is an infinite loop that looks like activity."""
    body = await call(mcp, "list_new_since", mark=10**12)

    assert body["returned"] == 0
    assert body["next_mark"] == 10**12


async def test_near_duplicates_are_not_handed_to_an_agent(mcp) -> None:
    """`P2-03` marked them for a reason. An agent walking the corpus to extract
    relations would otherwise derive the same edge repeatedly from the same
    boilerplate and record it as corroboration."""
    body = await call(mcp, "list_new_since", mark=0, limit=200)
    assert all(p.get("duplicate_of") is None for p in body["passages"])


# --------------------------------------------------------------------------
# It cannot write
# --------------------------------------------------------------------------


async def test_the_tools_run_on_the_read_only_role(session_for, mcp) -> None:
    """The surface is reachable by an external party; the guarantee that it
    cannot mutate anything must be Postgres's, not this module's.

    Checked against the catalogue rather than by attempting a write through a
    tool — there is no tool that writes, which is the stronger property, so
    there is nothing to attempt.
    """
    sess = await session_for("owner")
    for verb in ("INSERT", "UPDATE", "DELETE"):
        allowed = await sess.scalar(
            text("SELECT has_table_privilege('meridian_ro', 'sources', :verb)"), {"verb": verb}
        )
        assert allowed is False, f"the read role can {verb}"


async def test_a_missing_source_is_an_answer_not_a_crash(mcp) -> None:
    """An agent chasing a citation into a source that has been re-crawled away
    should be told so, not handed a stack trace it will paste into its reply."""
    body = await call(mcp, "get_source_metadata", source_id=10**12)
    assert "error" in body


# --------------------------------------------------------------------------
# Profiles name the tools the server has (`B-138`)
# --------------------------------------------------------------------------


async def test_every_profile_tool_exists_on_the_server(mcp) -> None:
    """A profile naming a missing tool grants a capability nobody can use, silently."""
    from meridian_core.grants import PROFILE_TOOLS

    registered = {tool.name for tool in await mcp.list_tools()}
    for profile, tools in PROFILE_TOOLS.items():
        assert tools <= registered, f"{profile} names tools the server lacks: {tools - registered}"


async def test_every_server_tool_is_in_some_profile(mcp) -> None:
    """A tool no profile carries can only be reached by a hand-scoped token."""
    from meridian_core.grants import PROFILE_TOOLS

    granted = set().union(*PROFILE_TOOLS.values())
    registered = {tool.name for tool in await mcp.list_tools()}
    assert registered <= granted, f"no profile carries: {registered - granted}"


# --------------------------------------------------------------------------
# What the site shows, as tools (`B-139`)
# --------------------------------------------------------------------------

SITE_TOOLS = (
    "find_nodes",
    "get_node",
    "find_route",
    "term_neighbourhood",
    "list_areas",
    "get_area",
    "list_gaps",
    "list_contested",
    "corpus_growth",
)


async def test_the_site_tools_are_registered_and_describe_themselves(mcp) -> None:
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    for name in SITE_TOOLS:
        assert name in tools, f"{name} is not registered"
        assert tools[name].description


def test_the_instructions_keep_cited_and_similar_apart(mcp) -> None:
    assert "CITED IS NOT SIMILAR" in mcp.instructions


async def test_the_map_tool_answers_what_the_map_page_answers(mcp, session_for) -> None:
    from meridian_core import areaview

    sess = await session_for("ro")
    expected = (await areaview.areas_level(sess, parent_id=None)).model_dump(mode="json")
    assert await call(mcp, "list_areas") == expected


async def test_the_contested_tool_answers_what_the_page_answers(mcp, session_for) -> None:
    from meridian_core import graphview

    sess = await session_for("ro")
    expected = (await graphview.contested_pairs(sess, limit=20)).model_dump(mode="json")
    assert await call(mcp, "list_contested", limit=20) == expected


async def test_a_missing_node_is_an_answer_not_a_crash(mcp) -> None:
    body = await call(mcp, "get_node", entity_id=10**9)
    assert "error" in body


async def test_node_search_and_gaps_answer(mcp) -> None:
    assert isinstance(await call(mcp, "find_nodes", query="a"), dict)
    gaps = await call(mcp, "list_gaps")
    assert "gaps" in gaps and "sources" in gaps


async def test_search_asks_for_a_query_vector(mcp, monkeypatch) -> None:
    """MCP search was lexical-only although the embedding service exists (`B-139`)."""
    from api.mcp import server

    asked: list[str] = []

    async def embed(query: str):
        asked.append(query)
        return None

    monkeypatch.setattr(server, "embed_query", embed)
    await call(mcp, "search_chunks", query="anything at all")
    assert asked == ["anything at all"]


# --------------------------------------------------------------------------
# Only what screening cleared reaches a model, on every path (`B-188`, §2.5)
# --------------------------------------------------------------------------


@pytest.fixture
async def three_trust_states(session_for):
    """One source per trust state, one passage each, committed and removed afterwards."""
    import uuid

    from sqlalchemy import delete, update

    from meridian_core.chunks import ChunkWrite, replace_chunks
    from meridian_core.models import Chunk, Source
    from meridian_core.sources import upsert_source

    marker = f"qxt{uuid.uuid4().hex[:10]}"
    sess = await session_for("rw")
    made: dict[str, int] = {}
    for state in ("cleared", "quarantined", "unscreened"):
        source, _ = await upsert_source(
            sess,
            f"https://{state}.{marker}.test/doc",
            checksum=f"sha256:{uuid.uuid4().hex}",
            source_tier="government",
            title=f"{state} {marker}",
        )
        await replace_chunks(
            sess,
            source.source_id,
            [ChunkWrite(text=f"{marker} a passage long enough to be a passage.", chunk_index=0)],
        )
        await sess.execute(
            update(Source).where(Source.source_id == source.source_id).values(trust_state=state)
        )
        made[state] = source.source_id
    await sess.commit()
    first = await sess.scalar(
        text("SELECT min(chunk_id) FROM chunks WHERE source_id = ANY(:ids)").bindparams(
            ids=list(made.values())
        )
    )
    yield made, first - 1

    await sess.execute(delete(Chunk).where(Chunk.source_id.in_(list(made.values()))))
    await sess.execute(delete(Source).where(Source.url.like(f"https://%.{marker}.test/%")))
    await sess.commit()


async def test_walking_the_corpus_hands_over_cleared_passages_only(mcp, three_trust_states) -> None:
    """§2.5: quarantined content is kept, never handed to a model; unscreened is not yet
    cleared. `search_chunks` applied this and `list_new_since` did not, so an assistant
    walking forward was given what one searching was refused."""
    made, mark = three_trust_states
    body = await call(mcp, "list_new_since", mark=mark, limit=200)
    ours = {p["source_id"] for p in body["passages"]} & set(made.values())
    assert ours == {made["cleared"]}


def test_every_passage_query_on_the_mcp_surface_uses_the_shared_predicate() -> None:
    """Read from the source: a tool that selects passages itself must narrow them with
    `readable_passage_conditions`, or with `search(..., cleared_only=True)`. A new tool
    that forgets fails here rather than on a live corpus."""
    import inspect
    import re

    import api.mcp.server as server

    source = inspect.getsource(server)
    selects = [m.start() for m in re.finditer(r"select\(Chunk\b", source)]
    for at in selects:
        window = source[at : at + 1200]
        assert "readable_passage_conditions()" in window, source[at : at + 200]
    for call_at in [m.start() for m in re.finditer(r"await search\(", source)]:
        # The filters are built just before the call.
        assert "cleared_only=True" in source[max(0, call_at - 1500) : call_at]
