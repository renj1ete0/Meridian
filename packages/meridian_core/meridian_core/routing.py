"""Which agent does a task, and what happens when it cannot (task `P4-07`, §11.3).

§11.3's argument is that swapping models should be a config row rather than a
code change, "which matters given how fast the options move". The registry has
existed since `P0-07` and nothing read it; this is the part that reads it.

**Nothing here calls a model.** Routing answers "who", the caller does the
asking, and keeping them apart is what makes every rule below testable against
rows rather than against a provider. It also means a provider outage is a
failure in one place instead of a branch in five.

Four rules, each with a failure it exists to prevent:

**A task type is a name from a fixed set.** `task_types` is a free text array,
so a row saying `tagging` where everything else says `tag_attributes` is not an
error anywhere — it is an agent that is simply never chosen, and the symptom is
that the expensive agent handles everything. That is what `TASK_TYPES` and its
drift test are for, and the seeded registry had exactly that typo in it.

**Disabled is never routed, and an empty `task_types` declares nothing.** Both
are the same position `budget.py` and `trust.py` take: absent is refused, not
waved through. A registry whose rows are all placeholders — which is how every
install starts — must refuse by saying so rather than by returning a row that
cannot answer.

**The chain skips what it cannot use and stops on a cycle.** `fallback_agent_id`
is a plain column with nothing stopping A → B → A, and a router that followed it
literally would hang the run rather than fail it. A link that is disabled, or
that does not declare the task, is stepped over rather than ending the chain —
one misconfigured row in the middle should not truncate everything behind it.

**The strongest agent is not the right agent.** §11.3 assigns each task a
*profile* — strongest for the hard reasoning, mid-tier for attribute tagging,
"any multilingual" for translation — and routing by raw quality would send
narrow schema-constrained work to the frontier model every time. That is the
bill the registry exists to avoid, and it is why `quality_tier` is ordinal and
separate from `cost_tier` (§11.12).

**Availability is not quality.** An `opportunistic` agent on a box that may be
asleep still routes first if it is the best one for the task; whether it answers
is the caller's problem, and that is precisely what the rest of the chain is
for. Ordering by anything but quality would mean quietly trading edge quality —
which §11.3 says "dominates everything downstream" — to avoid a wake-on-LAN
packet.
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

#: The quality tier each task is *aimed at*, from §11.3's table. Not "the
#: strongest available": the table says mid-tier for attribute tagging and
#: "any multilingual" for translation, and it says so deliberately — narrow,
#: schema-constrained work sent to the frontier model is the expense the whole
#: registry exists to avoid, and §11.12 keeps `quality_tier` ordinal and
#: separate from `cost_tier` precisely so this can be expressed.
#:
#: Where the table says "strongest", the target is the top of the scale, so the
#: best row wins whatever is configured. Where it says mid or mechanical, the
#: aim is a match rather than a maximum — and a stronger agent is still a
#: *fallback*, because overshooting costs money and undershooting costs quality.
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

    `min_context` is the one place absence is refused rather than assumed: an
    agent whose `max_context` nobody recorded cannot be shown to fit a payload,
    and finding that out from a provider error mid-run costs the run. A caller
    that does not know its payload size omits it and gets everything.
    """
    if not agent.enabled:
        return False
    if not agent.task_types or task_type not in agent.task_types:
        return False
    return min_context is None or (agent.max_context or 0) >= min_context


def _preference(agent: Agent, task_type: str) -> tuple[int, int, str]:
    """Sort key: closest to the task's target tier, then stronger, then by name.

    Distance rather than maximum, because §11.3 aims most tasks at a tier
    rather than at the top of the scale. Ties go to the *stronger* agent: both
    directions are wrong, but overshooting costs money and undershooting costs
    quality, and §16's position is that bad output is harder to detect than an
    invoice.

    An unrecorded tier reads as 0 and therefore sorts furthest from everything
    except the mechanical tasks — "nobody said" is not evidence of being good.
    `agent_id` last, so two runs over the same registry make the same choice; a
    route that varied would make a disagreement between runs unattributable.
    """
    tier = agent.quality_tier or 0
    return (abs(tier - TARGET_TIER[task_type]), -tier, agent.agent_id)


def resolve_chain(
    agents: Sequence[Agent], task_type: str, *, min_context: int | None = None
) -> list[Agent]:
    """The ordered list to try, best first. Pure.

    The head is the best eligible agent. What follows is its `fallback_agent_id`
    chain, which is walked rather than sorted — a fallback is a stated
    preference ("if this one is down, use that one"), and re-sorting it by
    quality would discard the statement.

    Returns `[]` when nothing is eligible; `route` is the one that raises, so a
    caller that wants to ask without handling an exception can.
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

    # Then everything else that can do the task, nearest the target first.
    #
    # **The stated chain is a preference, not the whole answer.** The seeded
    # registry points the mid tier at the frontier model, which does not
    # declare attribute tagging — so following `fallback_agent_id` alone leaves
    # attribute tagging with no fallback at all while a local agent that
    # declares it sits unused. §11.3 asks that an outage degrade rather than
    # halt, and a chain that stops at the first unusable pointer halts.
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

    Three different messages, because three different things are wrong and
    exactly one of them is fixed by editing a config row:

    - the registry is empty — nothing was seeded;
    - rows exist but none is enabled — the placeholders were never filled in,
      which is how every install starts;
    - rows are enabled but none declares this task, or none is large enough.
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
