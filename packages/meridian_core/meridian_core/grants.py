"""Access for somebody who is not the operator (tasks `P3-06`, `P3-10`, §3, §5).

The unit is a **person**, not a credential. Somebody given access will hold
several tokens — a browser session, an MCP client on a laptop, another on a
server — and revoking their access has to revoke all of them at once. Chasing
credentials one at a time is how the one you miss stays working.

**A profile, never a tool list.** §3 is firm: a free-form set of tools per
person is how somebody ends up holding a write tool nobody remembers granting.
Profiles are named, small, and defined here rather than in the database, so
adding a tool to `reader` is a code change somebody reviews.

**Two things a guest does not get by default** (§5): raw files, and anything
outside the topics named in the grant. Both are shaped so the restrictive
answer is what an absent value means.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import AgentToken, Grant, GrantAudit
from .search import SearchFilters
from .tiering import DEFAULT_TIER  # noqa: F401  (documents where tiers come from)

log = get_logger(__name__)

#: What each profile may call. Named sets, defined in code (§3).
#:
#: `reader` is the default and the one a person gets unless somebody decides
#: otherwise. `analyst` adds the tools that cost real money or real time.
#: `operator` is not "everything" — there is deliberately no write tool here,
#: because writes belong to the orchestrator's own credential and a grant is
#: for *reading* somebody else's corpus (§2.1).
PROFILE_TOOLS: dict[str, frozenset[str]] = {
    "reader": frozenset({"search_chunks", "get_source_metadata", "get_chunk"}),
    "analyst": frozenset(
        {"search_chunks", "get_source_metadata", "get_chunk", "run_readonly_query"}
    ),
    "operator": frozenset(
        {
            "search_chunks",
            "get_source_metadata",
            "get_chunk",
            "run_readonly_query",
            "export_markdown",
            "export_bibtex",
        }
    ),
}

#: Source tiers, most to least authoritative. `max_source_tier` names a floor
#: in this order: naming `academic` admits `government` and `academic` and
#: excludes everything below.
TIER_ORDER = ("government", "academic", "industry", "press", "informal")

__all__ = [
    "PROFILE_TOOLS",
    "TIER_ORDER",
    "GrantError",
    "ResolvedGrant",
    "filters_for",
    "may_read_raw",
    "calls_by_grant",
    "record_call",
    "within_rate_limit",
    "resolve_grant",
    "revoke_grant",
    "tiers_allowed",
]


class GrantError(RuntimeError):
    """The caller may not do this. Carries a reason, not just a message."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclasses.dataclass(frozen=True)
class ResolvedGrant:
    """A grant as it applies right now — expiry and revocation already applied.

    Frozen, and resolved once per request. A grant re-read mid-request could
    change between the tool check and the query, and the window between them is
    exactly where a revocation would be missed.
    """

    grant_id: int
    subject: str
    subject_kind: str
    profile: str
    topics: tuple[str, ...]
    max_source_tier: str | None
    raw_files: bool

    #: Whether this grant may see the operator's own annotations (§5).
    #:
    #: **False unless a grant says otherwise, and there is no column for it.**
    #: §12.5 predicts annotations become the highest-quality layer in the
    #: system precisely because they are the operator's own thinking — which
    #: makes them the most personal thing in it. Sharing them should be a
    #: deliberate act, and the deliberate act available today is the `operator`
    #: profile. A per-grant flag would make it a checkbox somebody ticks while
    #: setting up access for a colleague.
    @property
    def annotations(self) -> bool:
        return self.profile == "operator"

    @property
    def tools(self) -> frozenset[str]:
        """What this grant may call. An unknown profile grants nothing.

        Not an error: a profile added to the database by a future migration and
        not yet known to this code should fail closed, and a grant that can
        call nothing is a legible failure rather than a silent escalation.
        """
        return PROFILE_TOOLS.get(self.profile, frozenset())

    def may_call(self, tool: str) -> bool:
        return tool in self.tools


def tiers_allowed(max_source_tier: str | None) -> tuple[str, ...]:
    """Tiers at or above ``max_source_tier``, in authority order.

    An unknown tier admits nothing rather than everything. The alternative —
    treating an unrecognised ceiling as "no ceiling" — turns a typo into a
    widening of access, which is the wrong direction for a mistake to fail in.
    """
    if max_source_tier is None:
        return TIER_ORDER
    if max_source_tier not in TIER_ORDER:
        return ()
    return TIER_ORDER[: TIER_ORDER.index(max_source_tier) + 1]


def filters_for(grant: ResolvedGrant, base: SearchFilters | None = None) -> SearchFilters:
    """Narrow a search to what this grant may see (`P3-10`).

    **Intersects rather than replaces.** A guest may narrow their own search
    further — asking for one topic out of the three they hold — and that must
    not widen anything. Taking the intersection means the grant is a ceiling
    the caller cannot raise, whatever they send.

    `cleared_only` is forced on for the same reason the MCP surface forces it
    (`P4-14`): this is content going to somebody else's model.
    """
    base = base or SearchFilters()

    topics: tuple[str, ...] | None
    if grant.topics:
        asked = tuple(base.topics or ())
        topics = tuple(t for t in asked if t in grant.topics) if asked else grant.topics
        if not topics:
            # They asked only for topics they do not hold. Returning everything
            # they *do* hold would answer a question they did not ask; an empty
            # topic set that matches nothing is the honest answer.
            topics = ("\x00none",)
    else:
        topics = tuple(base.topics) if base.topics else None

    allowed = tiers_allowed(grant.max_source_tier)
    asked_tiers = tuple(base.source_tiers or ())
    tiers = tuple(t for t in asked_tiers if t in allowed) if asked_tiers else allowed
    if asked_tiers and not tiers:
        tiers = ("\x00none",)

    return dataclasses.replace(
        base,
        topics=topics,
        source_tiers=tiers,
        cleared_only=True,
    )


def may_read_raw(grant: ResolvedGrant) -> bool:
    """Whether this grant may be served raw files (§5).

    The raw store holds copies of third-party material kept as a research
    archive (§14.2). Serving those files to somebody else is redistribution,
    and it is a different act from sharing what the corpus *extracted* — a
    guest gets chunks, metadata and the source URL, which is a citation and is
    what a reader actually needs.

    Two conditions, not one: the grant must allow it *and* the deployment must
    serve raw files at all. `MERIDIAN_SERVE_RAW` is the operator's decision
    about their own instance, and a grant cannot overrule it.
    """
    return grant.raw_files


async def resolve_grant(
    sess: AsyncSession, subject: str, *, now: dt.datetime | None = None
) -> ResolvedGrant:
    """The live grant for ``subject``, or a refusal saying why.

    Three refusals with three reasons, because "you have no access", "your
    access was revoked" and "your access expired last Tuesday" are different
    messages to the person reading them and different signals in an audit log.
    """
    moment = now or dt.datetime.now(dt.UTC)

    row = (await sess.execute(select(Grant).where(Grant.subject == subject))).scalar_one_or_none()
    if row is None:
        raise GrantError("no_grant", f"{subject} has no access.")
    if row.revoked:
        raise GrantError("revoked", f"{subject}'s access was revoked.")
    if row.expires_at is not None and row.expires_at <= moment:
        raise GrantError("expired", f"{subject}'s access expired on {row.expires_at:%Y-%m-%d}.")

    return ResolvedGrant(
        grant_id=row.grant_id,
        subject=row.subject,
        subject_kind=row.subject_kind,
        profile=row.profile,
        topics=tuple(row.topics or ()),
        max_source_tier=row.max_source_tier,
        raw_files=row.raw_files,
    )


async def revoke_grant(sess: AsyncSession, grant_id: int) -> int:
    """Revoke a grant and every token beneath it. Returns how many tokens.

    One statement, which is the whole reason grants exist. Revoking the grant
    without the tokens would leave working credentials behind, and that is the
    failure this model is shaped to prevent — so they move together or the
    transaction does not commit.

    The tokens are marked revoked rather than deleted: a token row is what an
    audit log's entries point at, and deleting it would leave the history
    unable to say whose credential made a call.
    """
    grant = await sess.get(Grant, grant_id, with_for_update=True)
    if grant is None:
        raise GrantError("no_grant", f"No grant {grant_id}.")

    grant.revoked = True
    tokens = (
        (await sess.execute(select(AgentToken).where(AgentToken.grant_id == grant_id)))
        .scalars()
        .all()
    )
    for token in tokens:
        token.revoked = True

    await sess.flush()
    log.info(
        "grant revoked",
        extra={"grant_id": grant_id, "subject": grant.subject, "tokens": len(tokens)},
    )
    return len(tokens)


# ---------------------------------------------------------------------------
# Audit and rate limiting (task `P3-11`, §6)
# ---------------------------------------------------------------------------


async def record_call(
    sess: AsyncSession,
    grant: ResolvedGrant,
    tool: str,
    *,
    token_id: int | None = None,
    arguments: dict | None = None,
    rows: int | None = None,
    duration_ms: int | None = None,
    refused: str | None = None,
) -> None:
    """Record one tool call against its grant.

    Indexed by grant rather than token: "what has this person's model been
    reading" is the question, and once they hold three clients a per-token log
    cannot answer it.

    **Refusals are recorded too.** A log of successful calls answers half the
    question — a grant being repeatedly refused a tool is the more interesting
    signal, and it is exactly the one that disappears if only successes land
    here.

    Never raises. An audit write that took a read surface down would be the
    monitoring causing the outage, and a missing audit row is a smaller problem
    than a guest's client failing on a query that worked.
    """
    try:
        sess.add(
            GrantAudit(
                grant_id=grant.grant_id,
                token_id=token_id,
                tool=tool,
                arguments=arguments,
                rows=rows,
                duration_ms=duration_ms,
                refused=refused,
            )
        )
        await sess.flush()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.warning("could not record a grant call", extra={"reason": str(exc), "tool": tool})


async def within_rate_limit(
    sess: AsyncSession,
    grant: ResolvedGrant,
    *,
    token_id: int,
    limit: int | None,
    window_seconds: int = 3600,
    now: dt.datetime | None = None,
) -> bool:
    """Whether this token may make another call.

    **Per token, not per grant**, and that is deliberate even though everything
    else here is per grant. The thing being limited is a client in a retry
    loop, which §6 notes is otherwise indistinguishable from a crawl — and that
    is a property of one client, not of the person. Limiting the grant would
    mean one misbehaving laptop silences the same person's phone.

    `limit=None` means no limit, which is the opposite of the convention
    `budget.py` uses and is right here: a rate limit is a throttle on something
    already authorised, not an authorisation in itself. A token with no limit
    is a decision somebody made when they issued it; a *budget* with no cap is
    a decision nobody made.
    """
    if limit is None:
        return True
    if limit <= 0:
        return False

    moment = now or dt.datetime.now(dt.UTC)
    since = moment - dt.timedelta(seconds=window_seconds)
    used = int(
        await sess.scalar(
            select(func.count())
            .select_from(GrantAudit)
            .where(
                GrantAudit.token_id == token_id,
                GrantAudit.at >= since,
                # Refused calls do not count against the limit. Rate-limiting
                # somebody for calls they were not allowed to make turns one
                # misconfiguration into two, and the refusals are already
                # visible in the audit.
                GrantAudit.refused.is_(None),
            )
        )
        or 0
    )
    return used < limit


async def calls_by_grant(
    sess: AsyncSession, grant_id: int, *, limit: int = 100
) -> list[GrantAudit]:
    """What this person's model has been reading, newest first."""
    stmt = (
        select(GrantAudit)
        .where(GrantAudit.grant_id == grant_id)
        .order_by(GrantAudit.at.desc())
        .limit(limit)
    )
    return list((await sess.execute(stmt)).scalars().all())
