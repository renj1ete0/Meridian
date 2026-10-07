"""Which agent does a task, and what happens when it cannot (task `P4-07`, §11.3).

Reads the agent registry; nothing here calls a model. Task types come from a fixed set,
disabled rows and empty declarations are never routed, the fallback chain skips what it
cannot use and stops on a cycle, and agents are aimed at a task's tier rather than the
strongest. See docs/features/synthesis.md#routing-rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import QUALITY_TIER_MAX, QUALITY_TIER_MIN, Agent

log = get_logger(__name__)

__all__ = [
    "TARGET_TIER",
    "TASK_TYPES",
    "NoAgentAvailable",
    "chain_for",
    "eligible",
    "resolve_chain",
    "route",
]

#: The quality tier each task is aimed at, from §11.3's table: a match, not a
#: maximum. See docs/features/synthesis.md#routing-rules.
TARGET_TIER: Final[dict[str, int]] = {
    # "Strong reasoning, large context" — edge quality dominates downstream.
    "relation_extraction": QUALITY_TIER_MAX,
    # "Strongest available" — the genuinely hard reasoning.
    "gap_analysis": QUALITY_TIER_MAX,
    "analogical_expansion": QUALITY_TIER_MAX,
    # "Strongest, long context. Infrequent."
    "drafting": QUALITY_TIER_MAX,
    # "Mid-tier. Narrow, structured, schema-constrained."
    "tag_attributes": 3,
    # The local tier's own two (§11.7). Standing in for extraction wants a
    # little more than triage does, and neither wants the frontier model.
    "extraction_fallback": 2,
    # "Any multilingual. Mechanical."
    "translation": QUALITY_TIER_MIN,
    "triage": QUALITY_TIER_MIN,
    # "Ask the graph" (`P6-06`): answering a reader from retrieved passages.
    # Interactive, so the aim is whatever answers well enough quickly — a local
    # model first — rather than the frontier by default.
    "chat": 2,
}

#: Every task type the system routes. Derived, so a task with no target tier
#: cannot exist — the two would otherwise drift, and the half that forgot would
#: be the one that decides what a run costs.
TASK_TYPES: Final[frozenset[str]] = frozenset(TARGET_TIER)


class NoAgentAvailable(LookupError):
    """Nothing in the registry can do this task, and the message says why.

    A `LookupError` rather than a bespoke base: the caller's options are the
    same as for any other missing row, and §13.4 wants a deferred run rather
    than a crash — "if the model API is unreachable, skip and retry next cycle".
    """


def eligible(agent: Agent, task_type: str, *, min_context: int | None = None) -> bool:
    """Whether this row could do this task at all.

    With `min_context`, a row with no recorded `max_context` is refused. Omit it to
    skip the size check.
    """
    if not agent.enabled:
        return False
    if not agent.task_types or task_type not in agent.task_types:
        return False
    return min_context is None or (agent.max_context or 0) >= min_context


def _preference(agent: Agent, task_type: str) -> tuple[bool, int, int, int, str]:
    """Sort key: explicit route order first, then closest to the task's target tier.

    Rows with a ``route_order`` come first, lowest first (`B-137`, ADR 0002); the rest follow
    by tier, as below.

    Ties go to the stronger agent, an unrecorded tier reads as 0, and `agent_id` breaks
    the rest so the choice is deterministic.
    """
    tier = agent.quality_tier or 0
    ordered = agent.route_order is not None
    return (
        not ordered,
        agent.route_order if ordered else 0,
        abs(tier - TARGET_TIER[task_type]),
        -tier,
        agent.agent_id,
    )


def resolve_chain(
    agents: Sequence[Agent], task_type: str, *, min_context: int | None = None
) -> list[Agent]:
    """The ordered list to try, best first. Pure.

    The head is the best eligible agent, then its `fallback_agent_id` chain (walked, not
    sorted), then every other eligible row. Returns `[]` when nothing is eligible.
    """
    if task_type not in TASK_TYPES:
        # Not a refusal about the registry: the caller asked for something that
        # is not a task. Saying so here stops it being read as "no agent".
        raise ValueError(f"{task_type!r} is not a task type. Known: {sorted(TASK_TYPES)}")

    by_id = {agent.agent_id: agent for agent in agents}
    usable = sorted(
        (a for a in agents if eligible(a, task_type, min_context=min_context)),
        key=lambda a: _preference(a, task_type),
    )
    if not usable:
        return []

    # An explicit order is the whole statement (ADR 0002): following a fallback pointer
    # from the head would let one row's preference reorder the operator's list.
    if usable[0].route_order is not None:
        return usable

    chain = [usable[0]]
    in_chain = {usable[0].agent_id}
    visited = set(in_chain)
    cursor = usable[0].fallback_agent_id

    while cursor is not None and cursor not in visited:
        visited.add(cursor)
        nxt = by_id.get(cursor)
        if nxt is None:
            # A pointer to a row that does not exist. Worth saying out loud:
            # it is a registry that has been edited, and the fallback somebody
            # believes is there is not.
            log.warning(
                "fallback points at an unknown agent",
                extra={"agent": chain[-1].agent_id, "fallback": cursor},
            )
            break
        # Stepped over rather than ending the chain: one disabled row in the
        # middle should not hide everything behind it.
        if eligible(nxt, task_type, min_context=min_context) and nxt.agent_id not in in_chain:
            chain.append(nxt)
            in_chain.add(nxt.agent_id)
        cursor = nxt.fallback_agent_id

    # Then everything else that can do the task, nearest the target first: the stated
    # chain is a preference, not the whole answer (docs/features/synthesis.md#routing-rules).
    for agent in usable:
        if agent.agent_id not in in_chain:
            chain.append(agent)
            in_chain.add(agent.agent_id)

    return chain


async def chain_for(
    sess: AsyncSession, task_type: str, *, min_context: int | None = None
) -> list[Agent]:
    """`resolve_chain` over the whole registry.

    Loads every row rather than filtering in SQL. The registry is a handful of
    rows by design — §11.3 is a config table, not a workload — and the fallback
    walk needs rows a `WHERE task_types @> ...` would have excluded.
    """
    agents = list(await sess.scalars(select(Agent)))
    return resolve_chain(agents, task_type, min_context=min_context)


async def route(sess: AsyncSession, task_type: str, *, min_context: int | None = None) -> Agent:
    """The one agent to ask first, or a refusal that distinguishes the cases.

    Distinguishes an empty registry, no enabled row, and no row that declares the task
    or is large enough.
    """
    chain = await chain_for(sess, task_type, min_context=min_context)
    if chain:
        return chain[0]

    total = len(list(await sess.scalars(select(Agent))))
    enabled = len([a for a in await sess.scalars(select(Agent)) if a.enabled])

    if total == 0:
        raise NoAgentAvailable("the agent registry is empty; run the seed.")
    if enabled == 0:
        raise NoAgentAvailable(
            f"no agent is enabled ({total} configured). "
            "The seeded rows are placeholders: fill in a model and set enabled."
        )
    if min_context is not None:
        raise NoAgentAvailable(
            f"no enabled agent declares {task_type!r} with room for {min_context:,} tokens."
        )
    raise NoAgentAvailable(f"no enabled agent declares {task_type!r}.")
