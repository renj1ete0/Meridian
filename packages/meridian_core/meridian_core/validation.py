"""Server-side write validation (task P4-05, spec §11.8, §11.4, §2 principle 6).

> "Validate writes server-side. Anything enforced only by prompting will
> eventually be talked around."

**What this defends against, concretely.** The crawler fetches arbitrary web
pages. Those pages become chunks. Chunks are assembled into a batch and handed
to a model that holds `add_edge`, `tag_entity` and `enqueue_seed` (§11.6). A
page containing injected instructions is therefore not a theoretical attack on
this system — it is the ordinary path with hostile content in it, and §11.8
says so in as many words. This module is the part §11.8 calls load-bearing.

**Why a separate module rather than checks inside the write tools.** §11.1b:
three callers reach the same writes — the orchestrator's local functions, an
external agent over MCP, and an agent CLI holding a scoped write token — and
"none gets privileged access". Guards living inside one caller's code path are
guards the other two do not have. They live here so that adding a fourth caller
cannot accidentally mean adding a fourth, weaker, copy of the rules.

**Every function refuses by raising.** None of them return a boolean. A boolean
gets assigned to a variable that is then not checked, and the failure mode of
this module is silence — a guard that did not run looks exactly like a guard
that passed. `ValidationError` carries the rule it broke so a caller can hand
the model back something it can act on, and so a refusal is greppable in the
logs by rule rather than by message text.

**What this module deliberately does not do.** It does not close the relation
vocabulary. §5.4 lists node types exhaustively and does not list relation types,
so a fixed list here would be this module inventing schema — which AGENTS.md
says to ask about rather than do. `check_relation_type` refuses a *shape*, not
a value.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import Chunk, Entity, FetchPolicy, Run
from .netguard import BlockedTarget, check_scheme, normalise_address
from .tiering import registrable_domain

log = get_logger(__name__)

__all__ = [
    "MAX_RELATION_TYPE",
    "RESERVED_AUTHORS",
    "ValidationError",
    "check_chunks_resolve",
    "check_edge",
    "check_nodes_exist",
    "check_not_self_edge",
    "check_provenance",
    "check_relation_type",
    "check_seed_allowed",
    "check_tier_not_downgraded",
    "reserve_seeds",
]

#: `produced_by` values an *agent* may never write. `human` is reserved for the
#: reader's own annotations (`P6-05`), and that layer is only distinguishable
#: while nothing else can claim it. `scripts/seed.py` already refuses to
#: register an agent under this id; this refuses the write even if one existed.
RESERVED_AUTHORS = frozenset({"human"})

#: A relation type is an identifier a traversal groups by, not prose. The cap is
#: generous — `regulatory_requirement_applies_to` is 31 — and exists to refuse
#: the shape an injected instruction arrives in, where every edge would end up
#: its own relation and the grouping would mean nothing.
MAX_RELATION_TYPE = 64


class ValidationError(Exception):
    """A write that must not happen.

    ``rule`` is the machine-readable half: refusals are counted and alerted on
    by rule, and a message string that someone improves later would silently
    change what those counts mean.
    """

    def __init__(self, rule: str, detail: str) -> None:
        super().__init__(detail)
        self.rule = rule
        self.detail = detail


# ---------------------------------------------------------------------------
# §11.8: "add_edge rejects non-existent nodes"
# ---------------------------------------------------------------------------


async def check_nodes_exist(sess: AsyncSession, ids: Sequence[int]) -> None:
    """Every id names a row in `entities`, or nothing is written.

    The foreign key is not this check. It raises at flush — after a batch has
    been assembled, inside whatever transaction the caller had open — so the
    failure surfaces far from the tool call that caused it and takes the rest of
    the batch with it. Checking first means the model gets told which id was
    wrong while it still has the context to fix it.

    Named in §11.8 as the first mitigation for injected content, because "add an
    edge to node 99999999" is what a model does after reading a page that told
    it to.
    """
    wanted = {int(i) for i in ids}
    if not wanted:
        raise ValidationError("node_exists", "An edge needs two nodes; none were given.")

    found = set(await sess.scalars(select(Entity.entity_id).where(Entity.entity_id.in_(wanted))))
    missing = sorted(wanted - found)
    if missing:
        # Every missing id, not the first. A caller that fixes one and retries
        # only to be refused for the next is a retry loop, and the whole answer
        # costs the same single query.
        raise ValidationError(
            "node_exists",
            f"No such node{'' if len(missing) == 1 else 's'}: {missing}.",
        )


def check_not_self_edge(from_node: int, to_node: int) -> None:
    """Refuse an edge from a node to itself.

    Nothing in the schema forbids it and it is never a finding. "X relates to X"
    is what a model emits when it has resolved two mentions to the same node and
    not noticed — so a self-edge is a symptom of an entity-resolution failure
    (§5.5), and it is cheaper to refuse here than to find later in a traversal
    that will not terminate.
    """
    if from_node == to_node:
        raise ValidationError(
            "self_edge",
            f"Node {from_node} cannot be connected to itself; "
            "two mentions have probably resolved to one node.",
        )


# ---------------------------------------------------------------------------
# §2 principle 3: nothing is assertable without a citation you can follow
# ---------------------------------------------------------------------------


async def check_chunks_resolve(
    sess: AsyncSession, ids: Sequence[int], *, allow_empty: bool = False
) -> None:
    """Every cited chunk exists.

    ``allow_empty`` is the one place this differs from an annotation. A note may
    cite nothing, because its author is the justification (`P6-05`); a *derived*
    edge citing nothing cannot be re-derived from source chunks (§2.4) and
    cannot be checked by anyone, so for model writes the default stands.
    """
    wanted = {int(i) for i in ids}
    if not wanted:
        if allow_empty:
            return
        raise ValidationError(
            "chunk_resolves",
            "A derived write must name the chunks it came from (§2 principle 3).",
        )

    found = set(await sess.scalars(select(Chunk.chunk_id).where(Chunk.chunk_id.in_(wanted))))
    missing = sorted(wanted - found)
    if missing:
        raise ValidationError(
            "chunk_resolves",
            f"No such chunk{'' if len(missing) == 1 else 's'}: {missing}.",
        )


# ---------------------------------------------------------------------------
# §11.8: "enqueue_seed enforces a domain allowlist"
# ---------------------------------------------------------------------------


def _literal_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    candidate = host.strip("[]")
    try:
        return normalise_address(ipaddress.ip_address(candidate))
    except ValueError:
        return None


async def check_seed_allowed(
    sess: AsyncSession, url: str, *, allowed_schemes: Sequence[str] | None = None
) -> str:
    """Refuse a seed that must not be queued; return its registrable domain.

    Three refusals, in the order that makes the logged reason the useful one.

    **The scheme**, because `file:`, `gopher:` and `data:` are the classic SSRF
    escalations and a crawler speaks neither. `netguard` refuses these at fetch
    time too, and refusing them here as well is not redundant: a rejected seed
    that reached `queue` would sit there as `pending` and be retried with
    backoff, which turns a rejected injection into a scheduled one.

    **A literal private address**, judged without a DNS lookup because there is
    nothing to look up. This is where §11.8's attack path actually ends — a page
    saying "fetch http://169.254.169.254/latest/meta-data/" is asking the
    crawler to be a proxy into the network it runs on. Hostnames are *not*
    resolved here: DNS at seed time would be a second answer that can disagree
    with the one `netguard` gets at fetch time, and the fetch-time one is the
    one that matters (`P1-24`).

    **An operator's block.** `fetch_policy.status = 'blocked'` is a decision a
    person made, and a model must not be able to route around it by seeding the
    domain again.
    """
    if not url or not url.strip():
        raise ValidationError("seed_url", "A seed needs a URL.")

    try:
        check_scheme(url, list(allowed_schemes or ("http", "https")))
    except BlockedTarget as exc:
        raise ValidationError("seed_url", f"Refused: {exc}.") from exc

    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        raise ValidationError("seed_url", f"No host in {url!r}.")

    address = _literal_address(host)
    if address is not None and not address.is_global:
        raise ValidationError(
            "seed_url",
            f"{host} is not a public address; a seed must not point inside the network.",
        )

    domain = registrable_domain(host)
    status = await sess.scalar(select(FetchPolicy.status).where(FetchPolicy.domain == domain))
    if status == "blocked":
        raise ValidationError("domain_allowed", f"{domain} is blocked by fetch policy.")

    return domain


# ---------------------------------------------------------------------------
# §11.4/§11.9: "enqueue_seed enforces ... a per-run cap"
# ---------------------------------------------------------------------------


async def reserve_seeds(sess: AsyncSession, run_id: int, count: int, *, cap: int | None) -> None:
    """Take ``count`` seeds out of this run's budget, or refuse the lot.

    §11.9 describes the one feedback loop in this design that nothing else caps:
    gap analysis emits seeds, seeds become crawl targets, tomorrow's batch is
    larger, so more seeds are emitted. It compounds until crawl capacity
    saturates, and because the loop is unattended the first signal would be the
    bill.

    **`cap=None` refuses.** "Nobody configured a cap" must never read as
    "unlimited" — that is `P4-13`'s whole point and §16's ordering requirement,
    and a default of infinity is the shape in which a missing config becomes an
    incident.

    **All or nothing.** Partially admitting a batch would make the cap depend on
    the order the model happened to list its seeds in, and leave the caller
    unable to say which of its seeds were taken.

    **The row is locked, not just read.** Two tool calls reading `seeds_emitted`
    at 9 against a cap of 10 would both pass and both write. `SELECT ... FOR
    UPDATE` serialises them, which matters precisely because the caller here may
    be several concurrent tool invocations inside one run.
    """
    if cap is None or cap <= 0:
        raise ValidationError(
            "seed_cap",
            "No seed cap is configured for this run; refusing rather than "
            "treating an absent cap as unlimited (§16).",
        )
    if count <= 0:
        raise ValidationError("seed_cap", "A seed reservation must be for at least one seed.")

    run = (
        await sess.execute(select(Run).where(Run.run_id == run_id).with_for_update())
    ).scalar_one_or_none()
    if run is None:
        raise ValidationError("run_open", f"No run {run_id}.")
    if run.status != "running":
        # A completed run has had its budget accounted for. A write arriving
        # afterwards is a crashed worker resuming without reading state, or a
        # token being replayed; neither should extend the run's spend.
        raise ValidationError("run_open", f"Run {run_id} is {run.status}, not running.")

    if run.seeds_emitted + count > cap:
        raise ValidationError(
            "seed_cap",
            f"Run {run_id} has emitted {run.seeds_emitted} of {cap} seeds; "
            f"{count} more would exceed the cap.",
        )

    run.seeds_emitted += count
    await sess.flush()
    log.info(
        "seeds reserved",
        extra={"run": run_id, "count": count, "emitted": run.seeds_emitted, "cap": cap},
    )


# ---------------------------------------------------------------------------
# Provenance (§2.3, §11.12) — AGENTS.md's standing invariant
# ---------------------------------------------------------------------------


def check_provenance(
    *, produced_by: str | None, model: str | None, quality_tier: int | None
) -> None:
    """Every derived write names the agent, the exact model and the tier.

    Stated as an absolute in AGENTS.md, and the reason is downstream: §11.12's
    downgrade guard compares an incoming tier against the one already on the
    row, so a row missing its tier is a row nothing can protect. The agent and
    the model are what make `P7-10`'s precision sampling able to say *which*
    model produces bad edges rather than that some do.
    """
    missing = [
        name
        for name, value in (("produced_by", produced_by), ("model", model))
        if value is None or not str(value).strip()
    ]
    if quality_tier is None:
        missing.append("quality_tier")
    if missing:
        raise ValidationError(
            "provenance",
            f"A derived write must record {', '.join(missing)} (§2.3, §11.12).",
        )

    if produced_by in RESERVED_AUTHORS:
        raise ValidationError(
            "provenance",
            f"{produced_by!r} is reserved for the reader's own annotations "
            "and cannot be an agent (`P6-05`).",
        )


def check_tier_not_downgraded(*, existing: int | None, incoming: int | None) -> None:
    """A lower tier never silently overwrites a higher one (§11.12).

    The schedule is what makes this load-bearing rather than pedantic: nightly
    tier-2 tagging runs far more often than the frontier sessions that produce
    tier-4 edges (§11.1a), so without the guard the cheap work overwrites the
    good work on a timer, and the corpus quietly degrades while every individual
    run looks successful.

    An absent ``existing`` is not a higher tier — there is nothing to protect.
    """
    if existing is None or incoming is None:
        return
    if incoming < existing:
        raise ValidationError(
            "quality_tier",
            f"Refusing to overwrite tier {existing} with tier {incoming}; "
            "quality tier only moves up automatically (§11.12).",
        )


# ---------------------------------------------------------------------------
# Shape, not vocabulary
# ---------------------------------------------------------------------------


def check_relation_type(relation_type: str | None) -> None:
    """A relation type is an identifier, not prose.

    §5.4 closes the node-type ontology and deliberately leaves relations open,
    so this refuses a shape rather than a value — inventing a fixed list here
    would be inventing schema. The shape it refuses is the one injected
    instructions arrive in: free text in a column a traversal groups by, where
    every edge becomes its own relation and the grouping stops meaning anything.
    """
    if relation_type is None or not relation_type.strip():
        raise ValidationError("relation_type", "An edge needs a relation type.")

    value = relation_type.strip()
    if len(value) > MAX_RELATION_TYPE:
        raise ValidationError(
            "relation_type",
            f"Relation type is {len(value)} characters; the limit is {MAX_RELATION_TYPE}.",
        )
    if not value.replace("_", "").replace("-", "").isalnum():
        raise ValidationError(
            "relation_type",
            f"{value!r} is not an identifier; a relation type is a single token "
            "such as 'evaluates' or 'supersedes'.",
        )


# ---------------------------------------------------------------------------
# The composed check the write tools will call (`P4-04`)
# ---------------------------------------------------------------------------


async def check_edge(
    sess: AsyncSession,
    *,
    from_node: int,
    to_node: int,
    relation_type: str | None,
    supporting_chunk_ids: Sequence[int],
    produced_by: str | None,
    model: str | None,
    quality_tier: int | None,
) -> None:
    """Every guard `add_edge` needs, in one call.

    Offered so a write tool cannot pass four of the five checks by forgetting
    the fifth — the failure this module exists to make impossible is a guard
    that was never called, which looks identical to one that passed.

    Order is cheapest-first: the pure checks run before the two queries, so a
    malformed edge costs no database round trip. Nothing is written here, by
    anything, ever — a refusal must leave no row, or a rejected seed sits in the
    queue being retried with backoff and the rejection has scheduled the attack
    rather than stopped it.
    """
    check_not_self_edge(from_node, to_node)
    check_relation_type(relation_type)
    check_provenance(produced_by=produced_by, model=model, quality_tier=quality_tier)
    await check_nodes_exist(sess, [from_node, to_node])
    await check_chunks_resolve(sess, supporting_chunk_ids)
