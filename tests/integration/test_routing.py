"""Capability routing over the real registry (task `P4-07`, §11.3).

`tests/unit/test_routing.py` covers the rules. What needs a database is the
state every install actually starts in — a registry full of placeholder rows,
none of them enabled — and whether the refusal says something a person can act
on.

**The registry is restored, not rolled back**, in the one test that enables a
row: `route` does not commit, but a test that left an agent enabled would leave
the next synthesis run pointing at a model string that reads
`<fill in exact model string>`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select

from meridian_core.models import Agent
from meridian_core.routing import TASK_TYPES, NoAgentAvailable, chain_for, route

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def registry(session_for) -> AsyncIterator:
    sess = await session_for("rw")
    await sess.rollback()
    before = {
        row.agent_id: {"enabled": row.enabled, "task_types": list(row.task_types or [])}
        for row in await sess.scalars(select(Agent))
    }

    yield sess

    await sess.rollback()
    for row in await sess.scalars(select(Agent)):
        if row.agent_id in before:
            row.enabled = before[row.agent_id]["enabled"]
            row.task_types = before[row.agent_id]["task_types"]
    await sess.commit()


# --------------------------------------------------------------------------
# The state every install starts in
# --------------------------------------------------------------------------


async def test_the_seeded_registry_is_placeholders_and_says_so(registry) -> None:
    """Every row ships disabled with a model string nobody has filled in.

    The refusal has to distinguish that from an empty registry, because one is
    fixed by editing a row and the other by running the seed — and a single
    "no agent available" would send you to the wrong one.
    """
    with pytest.raises(NoAgentAvailable) as caught:
        await route(registry, "drafting")

    message = str(caught.value)
    assert "enabled" in message
    assert "placeholder" in message.lower()


async def test_an_empty_registry_says_to_seed_rather_than_to_edit(registry) -> None:
    # Deleted inside the transaction and never committed: `route` only reads.
    await registry.execute(delete(Agent))

    with pytest.raises(NoAgentAvailable, match="empty"):
        await route(registry, "drafting")

    await registry.rollback()


async def test_enabling_one_row_is_all_it_takes(registry) -> None:
    """§11.3's whole claim: swapping models is a config row, not a code change."""
    row = await registry.get(Agent, "hosted-frontier")
    assert row is not None, "the seeded registry is missing its frontier row"
    row.enabled = True
    await registry.flush()

    chosen = await route(registry, "drafting")

    assert chosen.agent_id == "hosted-frontier"


async def test_a_task_no_enabled_agent_declares_names_the_task(registry) -> None:
    row = await registry.get(Agent, "hosted-frontier")
    row.enabled = True
    row.task_types = ["drafting"]
    await registry.flush()

    with pytest.raises(NoAgentAvailable, match="translation"):
        await route(registry, "translation")


async def test_a_payload_too_large_for_anything_says_that_rather_than_no_agent(registry) -> None:
    # A different fix again: this one is "use a smaller payload or configure a
    # bigger agent", not "enable something".
    row = await registry.get(Agent, "hosted-frontier")
    row.enabled = True
    await registry.flush()

    with pytest.raises(NoAgentAvailable, match="room for"):
        await route(registry, "drafting", min_context=10_000_000)


async def test_the_registry_in_the_database_names_only_task_types_that_route(registry) -> None:
    """The YAML check is not enough, because the seed is insert-only.

    `scripts/seed.py` skips a row that already exists, deliberately: re-seeding
    must not undo steering. So a task type corrected in `config/agents.yaml`
    stays wrong in every database seeded before the correction, and nothing
    that reads the YAML can see it. Only a migration reaches those rows, and
    this is the test that says whether one has run here.
    """
    named = {
        task for row in await registry.scalars(select(Agent)) for task in (row.task_types or [])
    }

    unknown = named - TASK_TYPES
    assert not unknown, (
        f"the agents table names task types nothing routes: {sorted(unknown)}. "
        "Fixing config/agents.yaml does not reach an already-seeded database."
    )


# --------------------------------------------------------------------------
# The chain, over rows that were actually seeded
# --------------------------------------------------------------------------


async def test_attribute_tagging_goes_to_the_mid_tier_and_degrades_downward(registry) -> None:
    """§11.3's table says mid-tier for attribute tagging, and the seed obeys it.

    The interesting part is what follows. `hosted-mid` points its fallback at
    `hosted-frontier`, which does not declare this task — so the *stated* chain
    is one agent long, and following it alone would leave attribute tagging
    with no fallback at all while the local agent that declares it sits unused.
    """
    for agent_id in ("hosted-frontier", "hosted-mid", "local-llamacpp"):
        row = await registry.get(Agent, agent_id)
        row.enabled = True
    await registry.flush()

    chain = [a.agent_id for a in await chain_for(registry, "tag_attributes")]

    assert chain == ["hosted-mid", "local-llamacpp"]
    assert "hosted-frontier" not in chain, "it does not declare the task, however strong it is"


async def test_the_hard_reasoning_goes_to_the_frontier_row(registry) -> None:
    for agent_id in ("hosted-frontier", "hosted-mid", "local-llamacpp"):
        row = await registry.get(Agent, agent_id)
        row.enabled = True
    await registry.flush()

    for task in ("relation_extraction", "gap_analysis", "analogical_expansion", "drafting"):
        chain = await chain_for(registry, task)
        assert chain[0].agent_id == "hosted-frontier", task


async def test_a_chain_never_repeats_an_agent(registry) -> None:
    """The stated walk and the sweep that follows it could otherwise overlap.

    A repeated agent means a retry against something that has already failed,
    which spends a call and the time to make it on a known answer.
    """
    for row in await registry.scalars(select(Agent)):
        row.enabled = True
    await registry.flush()

    for task in sorted(TASK_TYPES):
        ids = [a.agent_id for a in await chain_for(registry, task)]
        assert len(ids) == len(set(ids)), f"{task}: {ids}"


async def test_every_task_type_routes_somewhere_once_everything_is_enabled(registry) -> None:
    """Coverage over the rows as seeded, not as written in a fixture.

    A task type with no agent is a run that gets all the way to needing it
    before refusing, which is the most expensive moment to find out.
    """
    for row in await registry.scalars(select(Agent)):
        row.enabled = True
    await registry.flush()

    for task_type in sorted(TASK_TYPES):
        chain = await chain_for(registry, task_type)
        assert chain, f"nothing routes {task_type}"
