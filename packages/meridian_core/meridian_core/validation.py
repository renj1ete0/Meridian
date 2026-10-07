"""Server-side write validation (task P4-05, spec §11.8, §11.4, §2 principle 6).

Every guard refuses by raising :class:`ValidationError`, which names the rule broken.
The relation vocabulary is left open. See
docs/features/knowledge-graph.md#writes-and-validation.
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
from .trust import FRONTIER_DISCOVERY

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

#: `produced_by` values an *agent* may never write: `human` marks the reader's own
#: annotations (`P6-05`).
RESERVED_AUTHORS = frozenset({"human"})

#: Longest relation type accepted: generous for an identifier, short of prose.
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

    Checked before the foreign key would raise at flush, so the model is told which id
    was wrong (§11.8).
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

    Never a finding; a symptom of a resolution failure (§5.5).
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

    ``allow_empty`` is for annotations (`P6-05`), whose author is the justification; a
    derived edge must cite something.
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
    sess: AsyncSession,
    url: str,
    *,
    allowed_schemes: Sequence[str] | None = None,
    require_seed_allowed: bool = False,
) -> str:
    """Refuse a seed that must not be queued; return its registrable domain.

    Refuses, in order: a non-web scheme, a literal private address (no DNS lookup;
    `netguard` resolves at fetch time), an operator-blocked domain, and, with
    ``require_seed_allowed``, a domain nobody approved (`P4-12`). See
    docs/features/knowledge-graph.md#writes-and-validation.
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
    row = (
        await sess.execute(select(FetchPolicy).where(FetchPolicy.domain == domain))
    ).scalar_one_or_none()
    if row is not None and row.status == "blocked":
        raise ValidationError("domain_allowed", f"{domain} is blocked by fetch policy.")

    # `P4-12`. False (declined) and None (not yet looked at) get different messages,
    # because they lead to different actions.
    if require_seed_allowed and row is not None and row.seed_allowed is not True:
        if row.seed_allowed is False:
            raise ValidationError("domain_allowed", f"{domain} is not allowed for seeding.")
        if row.first_seen_via not in FRONTIER_DISCOVERY:
            raise ValidationError(
                "domain_allowed",
                f"{domain} has not been approved for seeding yet; it is waiting "
                f"for a decision in Admin.",
            )

    return domain


# ---------------------------------------------------------------------------
# §11.4/§11.9: "enqueue_seed enforces ... a per-run cap"
# ---------------------------------------------------------------------------


async def reserve_seeds(sess: AsyncSession, run_id: int, count: int, *, cap: int | None) -> None:
    """Take ``count`` seeds out of this run's budget, or refuse the lot.

    ``cap=None`` refuses (`P4-13`). The run row is locked (``FOR UPDATE``) so concurrent
    tool calls cannot both pass. See docs/features/knowledge-graph.md#writes-and-validation.
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

    The downgrade guard needs the tier, and `P7-10`'s sampling the agent and model.
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

    An absent ``existing`` is not a higher tier: there is nothing to protect.
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

    Refuses a shape (length, non-identifier characters), not a value: relations are
    open (§5.4).
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

    Cheapest first: the pure checks run before the two queries. Writes nothing.
    """
    check_not_self_edge(from_node, to_node)
    check_relation_type(relation_type)
    check_provenance(produced_by=produced_by, model=model, quality_tier=quality_tier)
    await check_nodes_exist(sess, [from_node, to_node])
    await check_chunks_resolve(sess, supporting_chunk_ids)
