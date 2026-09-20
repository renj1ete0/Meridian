"""Access for somebody who is not the operator (task `P3-06`, §3).

The unit is a **person**, not a credential. Somebody given access will hold
several tokens — a browser session, an MCP client on a laptop, another on a
server — and revoking their access has to revoke all of them at once. Chasing
credentials one at a time is how the one you miss stays working.

**A profile, never a tool list.** §3 is firm: a free-form set of tools per
person is how somebody ends up holding a write tool nobody remembers granting.
Profiles are named, small, and defined here rather than in the database, so
adding a tool to `reader` is a code change somebody reviews.

What a grant *scopes* — topics, source tiers, raw files — is `P3-10`. This
module is the grant itself: who holds it, whether it is still live, and what
revoking it takes with it.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import AgentToken, Grant

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

__all__ = [
    "PROFILE_TOOLS",
    "GrantError",
    "ResolvedGrant",
    "resolve_grant",
    "revoke_grant",
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
