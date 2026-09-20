"""Capability routing, without a database (task `P4-07`, §11.3).

The registry is a config table, so almost everything that can go wrong with it
is a row somebody typed: a task type spelled differently from everywhere else,
a fallback pointing at a deleted agent, a chain that loops. None of those is an
error to Postgres and none of them raises — the symptom is an agent that is
never chosen, or a run that hangs.

The two drift tests are why this file exists. One fails when the seeded
registry names a task type nothing routes; it caught `local-llamacpp`
advertising `tagging` while every other row and every caller said
`tag_attributes`, so the local tier would have sat idle and the hosted agent
would have done attribute tagging at hosted prices. The other fails when a task
type has no agent at all, which is a run refusing at the moment it is needed.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from meridian_core.models import QUALITY_TIER_MAX, QUALITY_TIER_MIN, Agent
from meridian_core.routing import TARGET_TIER, TASK_TYPES, eligible, resolve_chain

REGISTRY = pathlib.Path(__file__).resolve().parents[2] / "config/agents.yaml"


def agent(agent_id: str, **kwargs) -> Agent:
    """A registry row with the defaults a usable agent needs."""
    defaults = {
        "provider": "test",
        "task_types": ["drafting"],
        "enabled": True,
        "quality_tier": 3,
        "max_context": 100_000,
        "fallback_agent_id": None,
    }
    return Agent(agent_id=agent_id, **{**defaults, **kwargs})


# --------------------------------------------------------------------------
# Drift against the seeded registry
# --------------------------------------------------------------------------


def test_every_seeded_task_type_is_one_that_routes() -> None:
    """A task type is a free string, so a typo is a silent no-op.

    `task_types` is `text[]`. Postgres accepts anything, `resolve_chain`
    matches on equality, and an agent naming a type nothing asks for is simply
    never selected — the visible symptom is the expensive agent doing work the
    cheap one was configured for.
    """
    rows = yaml.safe_load(REGISTRY.read_text())["agents"]
    named = {task for row in rows for task in (row.get("task_types") or [])}

    unknown = named - TASK_TYPES
    assert not unknown, f"config/agents.yaml names task types nothing routes: {sorted(unknown)}"


def test_every_task_type_has_an_agent_configured_for_it() -> None:
    """The opposite drift: a task nothing can do.

    Adding a task type without a registry row means a run that gets all the way
    to needing it before refusing, which is the most expensive moment to find
    out. The rows ship disabled — this is about coverage, not readiness.
    """
    rows = yaml.safe_load(REGISTRY.read_text())["agents"]
    covered = {task for row in rows for task in (row.get("task_types") or [])}

    orphaned = TASK_TYPES - covered
    assert not orphaned, f"no seeded agent declares: {sorted(orphaned)}"


def test_every_task_aims_at_a_tier_that_exists() -> None:
    # A target outside the scale is a task nothing can ever match well, and the
    # arithmetic would hide it: the distance is still finite, so routing would
    # silently pick whatever is nearest rather than refusing.
    for task, tier in TARGET_TIER.items():
        assert QUALITY_TIER_MIN <= tier <= QUALITY_TIER_MAX, f"{task} aims at tier {tier}"


def test_an_unknown_task_type_is_refused_as_a_bad_argument() -> None:
    # Distinct from "no agent": one is fixed by editing a config row and the
    # other by fixing the caller, and a single message would send you looking
    # in the wrong place.
    with pytest.raises(ValueError, match="not a task type"):
        resolve_chain([agent("a")], "summarise_everything")


# --------------------------------------------------------------------------
# What may be routed
# --------------------------------------------------------------------------


def test_a_disabled_agent_is_never_chosen() -> None:
    # Every install starts with placeholder rows, all disabled.
    assert resolve_chain([agent("a", enabled=False)], "drafting") == []


@pytest.mark.parametrize("declared", [None, []])
def test_an_agent_that_declares_no_task_does_nothing(declared) -> None:
    # Absent is refused, as in `budget.py` and `trust.py`: an empty list is not
    # a wildcard, or an unconfigured row would quietly handle everything.
    assert resolve_chain([agent("a", task_types=declared)], "drafting") == []


def test_an_agent_that_declares_another_task_is_not_chosen() -> None:
    assert resolve_chain([agent("a", task_types=["translation"])], "drafting") == []


def test_a_task_that_wants_the_strongest_gets_the_strongest() -> None:
    chain = resolve_chain(
        [agent("cheap", quality_tier=1), agent("best", quality_tier=4), agent("mid")], "drafting"
    )

    assert chain[0].agent_id == "best"


def test_a_mid_tier_task_does_not_go_to_the_frontier_model() -> None:
    """§11.3 says mid-tier for attribute tagging, and means it.

    Narrow, structured, schema-constrained work sent to the strongest agent is
    the bill the registry exists to avoid — and routing by raw quality would do
    it on every run, invisibly, because the output would be fine.
    """
    chain = resolve_chain(
        [
            agent("frontier", quality_tier=4, task_types=["tag_attributes"]),
            agent("mid", quality_tier=3, task_types=["tag_attributes"]),
            agent("small", quality_tier=1, task_types=["tag_attributes"]),
        ],
        "tag_attributes",
    )

    assert chain[0].agent_id == "mid"
    # Still a fallback, and ahead of the weaker one: overshooting costs money,
    # undershooting costs quality, and §16 makes bad output the worse failure.
    assert [a.agent_id for a in chain] == ["mid", "frontier", "small"]


def test_a_mechanical_task_prefers_the_cheap_agent() -> None:
    chain = resolve_chain(
        [
            agent("frontier", quality_tier=4, task_types=["translation"]),
            agent("small", quality_tier=1, task_types=["translation"]),
        ],
        "translation",
    )

    assert chain[0].agent_id == "small"


def test_an_unrecorded_quality_tier_is_not_treated_as_a_good_one() -> None:
    # "Nobody said" is not evidence of being good, and §11.3 makes edge quality
    # the thing that dominates everything downstream.
    chain = resolve_chain(
        [agent("unknown", quality_tier=None), agent("known", quality_tier=1)], "drafting"
    )

    assert chain[0].agent_id == "known"


def test_a_tie_is_broken_the_same_way_every_time() -> None:
    """A route that varied between runs would make a disagreement between two
    runs impossible to attribute to anything."""
    rows = [agent("zeta"), agent("alpha"), agent("mu")]

    assert resolve_chain(rows, "drafting")[0].agent_id == "alpha"
    assert resolve_chain(list(reversed(rows)), "drafting")[0].agent_id == "alpha"


# --------------------------------------------------------------------------
# The fallback chain
# --------------------------------------------------------------------------


def test_the_stated_fallback_is_tried_before_anything_else_that_could() -> None:
    """A fallback is a stated preference, and it outranks a merely-eligible row.

    The point of `fallback_agent_id` is "if this one is down, use *that* one",
    which is a claim about availability and cost that no column can express.
    Everything else able to do the task still follows it, so an outage degrades
    rather than halting — but it follows, it does not displace.
    """
    chain = resolve_chain(
        [
            agent("head", quality_tier=2, fallback_agent_id="stated"),
            agent("stated", quality_tier=1),
            agent("unrelated", quality_tier=1),
        ],
        "drafting",
    )

    assert [a.agent_id for a in chain] == ["head", "stated", "unrelated"]


def test_an_agent_outside_the_stated_chain_is_still_a_fallback() -> None:
    """Following `fallback_agent_id` alone can leave a task with no fallback.

    The seeded registry points the mid tier at the frontier model, which does
    not declare attribute tagging — so the stated chain for that task is one
    agent long while a local agent that *does* declare it sits unused. §11.3
    asks that an outage degrade rather than halt, and a chain that stops at the
    first unusable pointer halts.
    """
    chain = resolve_chain(
        [
            agent("mid", quality_tier=3, fallback_agent_id="cannot", task_types=["tag_attributes"]),
            agent("cannot", quality_tier=4, task_types=["translation"]),
            agent("local", quality_tier=2, task_types=["tag_attributes"]),
        ],
        "tag_attributes",
    )

    assert [a.agent_id for a in chain] == ["mid", "local"]


def test_a_cycle_ends_the_chain_instead_of_hanging_the_run() -> None:
    """`fallback_agent_id` is a plain column and nothing stops A → B → A.

    A router that followed it literally would hang rather than fail, and a hung
    synthesis run looks exactly like a slow one.
    """
    chain = resolve_chain(
        [
            agent("a", quality_tier=4, fallback_agent_id="b"),
            agent("b", fallback_agent_id="a"),
        ],
        "drafting",
    )

    assert [x.agent_id for x in chain] == ["a", "b"]


def test_a_self_referencing_fallback_terminates() -> None:
    chain = resolve_chain([agent("a", fallback_agent_id="a")], "drafting")

    assert [x.agent_id for x in chain] == ["a"]


def test_a_fallback_that_cannot_do_the_task_is_stepped_over_not_a_dead_end() -> None:
    """One misconfigured row in the middle must not hide everything behind it.

    This is the difference between a chain that degrades and a chain that
    truncates, and the truncated one fails at the moment the head is down —
    which is the moment the rest of the chain existed for.
    """
    chain = resolve_chain(
        [
            agent("head", quality_tier=4, fallback_agent_id="wrong-task"),
            agent("wrong-task", task_types=["translation"], fallback_agent_id="usable"),
            agent("usable", quality_tier=1),
        ],
        "drafting",
    )

    assert [x.agent_id for x in chain] == ["head", "usable"]


def test_a_disabled_link_is_stepped_over_too() -> None:
    chain = resolve_chain(
        [
            agent("head", quality_tier=4, fallback_agent_id="off"),
            agent("off", enabled=False, fallback_agent_id="on"),
            agent("on", quality_tier=1),
        ],
        "drafting",
    )

    assert [x.agent_id for x in chain] == ["head", "on"]


def test_a_fallback_pointing_at_a_deleted_agent_ends_the_chain_quietly() -> None:
    # A registry that has been edited. The head still works, which is what
    # matters; the loss is logged rather than raised.
    chain = resolve_chain([agent("head", fallback_agent_id="gone")], "drafting")

    assert [x.agent_id for x in chain] == ["head"]


# --------------------------------------------------------------------------
# Context size
# --------------------------------------------------------------------------


def test_an_agent_too_small_for_the_payload_is_not_offered() -> None:
    chain = resolve_chain(
        [agent("small", quality_tier=4, max_context=8_000), agent("big", quality_tier=1)],
        "drafting",
        min_context=50_000,
    )

    assert [x.agent_id for x in chain] == ["big"]


def test_an_unrecorded_context_window_is_refused_when_a_size_is_asked_for() -> None:
    """Absent is refused, and the seeded local row has `max_context: null`.

    An agent that cannot be *shown* to fit is one whose failure arrives as a
    provider error partway through a run, which costs the run rather than the
    call.
    """
    assert resolve_chain([agent("a", max_context=None)], "drafting", min_context=1) == []


def test_an_unrecorded_context_window_is_fine_when_nothing_asked() -> None:
    # A caller that does not know its payload size should not be excluding
    # agents on a column nobody filled in.
    assert eligible(agent("a", max_context=None), "drafting")
    assert len(resolve_chain([agent("a", max_context=None)], "drafting")) == 1
