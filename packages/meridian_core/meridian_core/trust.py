"""What the crawl is willing to hand a model (task `P4-14`, §2.5, §11.8).

A domain's screening verdict is cached on `fetch_policy` and paid once per domain.
Quarantined content is stored, never deleted, and only `cleared` reaches a model
(`unscreened` does not). A curated domain clears on sight; any other clears after
`CLEAN_FETCHES_TO_CLEAR` clean fetches in a row. See docs/features/source-quality.md#trust.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import Chunk, FetchPolicy, Source

log = get_logger(__name__)

#: Consecutive unflagged fetches before an unknown domain clears itself.
#: See docs/features/source-quality.md#trust.
CLEAN_FETCHES_TO_CLEAR = 5

#: The states whose content the slow loop may read.
READABLE_STATES = ("cleared",)

#: Set by the crawl itself, so a screen can tell an automatic verdict from a
#: judgement somebody made.
DECIDED_BY_TIER = "auto:tier"
DECIDED_BY_CLEAN = "auto:clean"
DECIDED_BY_SCREEN = "auto:screen"

__all__ = [
    "CLEAN_FETCHES_TO_CLEAR",
    "DECIDED_BY_CLEAN",
    "DECIDED_BY_SCREEN",
    "DECIDED_BY_TIER",
    "FRONTIER_DISCOVERY",
    "NOVEL_FETCHES_TO_ALLOW",
    "OPERATOR_CHOSEN",
    "READABLE_STATES",
    "awaiting_seed_approval",
    "only_readable",
    "page_state",
    "readable_chunk_ids",
    "record_discovery",
    "record_novel_fetch",
    "record_screening",
]


def page_state(domain_state: str, *, flagged: bool) -> str:
    """The state a page is stored under, given its domain's verdict.

    A flagged page on an unscreened domain is `quarantined` on its own account; a
    flagged page on a *cleared* domain stays cleared. See
    docs/features/source-quality.md#trust.
    """
    if domain_state == "rejected":
        return "rejected"
    if domain_state == "cleared":
        return "cleared"
    # `unscreened` or `quarantined`, from here.
    if flagged:
        return "quarantined"
    return domain_state


async def record_screening(
    sess: AsyncSession,
    domain: str,
    *,
    flagged: bool,
    tier_mapped: bool,
    now: dt.datetime | None = None,
) -> str:
    """Fold one fetch's screening result into the domain's verdict.

    Returns the domain's state *after* this fetch, which the caller stores on the
    page. One row, no history table. A `rejected` domain is left alone: rejection is
    a person's decision.
    """
    moment = now or dt.datetime.now(dt.UTC)
    row = await sess.get(FetchPolicy, domain, with_for_update=True)
    if row is None:
        row = FetchPolicy(domain=domain)
        sess.add(row)
        await sess.flush()

    if row.trust_state == "rejected":
        return "rejected"

    if flagged:
        # The counter resets whatever the state: a cleared domain stays cleared but
        # starts earning its clearing again.
        row.clean_fetches = 0
        if row.trust_state == "unscreened":
            row.trust_state = "quarantined"
            row.trust_decided_at = moment
            row.trust_decided_by = DECIDED_BY_SCREEN
            row.trust_reason = "the injection pre-screen flagged a page from this domain"
            log.warning(
                "domain quarantined",
                extra={"domain": domain, "reason": row.trust_reason},
            )
        await sess.flush()
        return row.trust_state

    row.clean_fetches += 1

    if row.trust_state == "cleared":
        await sess.flush()
        return "cleared"

    if tier_mapped and row.trust_state == "unscreened":
        row.trust_state = "cleared"
        row.trust_decided_at = moment
        row.trust_decided_by = DECIDED_BY_TIER
        row.trust_reason = "the domain is in the curated source-tier map"
        log.info("domain cleared", extra={"domain": domain, "by": DECIDED_BY_TIER})
    elif row.trust_state == "unscreened" and row.clean_fetches >= CLEAN_FETCHES_TO_CLEAR:
        row.trust_state = "cleared"
        row.trust_decided_at = moment
        row.trust_decided_by = DECIDED_BY_CLEAN
        row.trust_reason = f"{row.clean_fetches} consecutive fetches passed the pre-screen"
        log.info(
            "domain cleared",
            extra={"domain": domain, "by": DECIDED_BY_CLEAN, "clean_fetches": row.clean_fetches},
        )

    await sess.flush()
    return row.trust_state


def only_readable(stmt: Select, *, states: Sequence[str] = READABLE_STATES) -> Select:
    """Narrow a chunk query to what the slow loop may read.

    Applied to the chunk query, as `IN (cleared)`. Deliberately **not** applied to
    the operator's own search. See docs/features/source-quality.md#trust.
    """
    return stmt.where(Source.trust_state.in_(tuple(states)))


def readable_chunk_ids(states: Sequence[str] = READABLE_STATES) -> Select:
    """The chunk ids the slow loop may read, as a subquery-able select.

    For callers that cannot thread a filter through an existing query — the
    high-water-mark scan in particular, which walks `chunks` by id and has no
    `sources` join to hang a predicate on.
    """
    return select(Chunk.chunk_id).join(Source).where(Source.trust_state.in_(tuple(states)))


# ---------------------------------------------------------------------------
# Whether a domain may be seeded at all (task `P4-12`, §11.4)
# ---------------------------------------------------------------------------
# A third question beside `status` and `trust_state`: *which domains* §11.4's cap on
# model seeding applies within. See docs/features/source-quality.md#seeding-a-domain.

#: Novel documents a frontier-discovered domain must return before it may be
#: seeded freely. Novel, not fetched: a site serving one page under a thousand
#: URLs would otherwise approve itself on volume alone.
NOVEL_FETCHES_TO_ALLOW = 3

#: How a domain can arrive without anyone having chosen it. These are the
#: crawl's own discoveries — it followed a link, read a sitemap, resolved a
#: citation — and they auto-approve on evidence.
FRONTIER_DISCOVERY = ("frontier", "sitemap", "search", "citation", "doi")

#: These do not. `user` is somebody typing a URL, which is consent; `model` and
#: `diversity` are a model's suggestion, which is a proposal.
OPERATOR_CHOSEN = ("user",)


async def record_discovery(sess: AsyncSession, domain: str, *, seed_source: str) -> FetchPolicy:
    """Note how a domain first became known, without overwriting the answer.

    Called wherever a URL is queued. The first `seed_source` sticks. An operator's own
    seed is allowed immediately. See docs/features/source-quality.md#seeding-a-domain.
    """
    row = await sess.get(FetchPolicy, domain, with_for_update=True)
    if row is None:
        row = FetchPolicy(domain=domain)
        sess.add(row)

    if row.first_seen_via is None:
        row.first_seen_via = seed_source
        if seed_source in OPERATOR_CHOSEN:
            row.seed_allowed = True

    await sess.flush()
    return row


async def record_novel_fetch(
    sess: AsyncSession, domain: str, *, now: dt.datetime | None = None
) -> bool | None:
    """Count a novel document from ``domain``, and approve it if it has earned it.

    Returns the domain's `seed_allowed` after the count. A domain first sighted in a
    model's proposal is **not** approved by this and waits for a person; see
    docs/features/source-quality.md#seeding-a-domain.
    """
    row = await sess.get(FetchPolicy, domain, with_for_update=True)
    if row is None:
        row = FetchPolicy(domain=domain)
        sess.add(row)
        await sess.flush()

    row.novel_fetches += 1

    if (
        row.seed_allowed is None
        and row.first_seen_via in FRONTIER_DISCOVERY
        and row.novel_fetches >= NOVEL_FETCHES_TO_ALLOW
    ):
        row.seed_allowed = True
        log.info(
            "domain allowed for seeding",
            extra={
                "domain": domain,
                "novel_fetches": row.novel_fetches,
                "first_seen_via": row.first_seen_via,
            },
        )

    await sess.flush()
    return row.seed_allowed


async def awaiting_seed_approval(sess: AsyncSession) -> list[FetchPolicy]:
    """Domains a model proposed that nobody has ruled on.

    The queue an admin screen shows, the same shape as the gazetteer's
    (§5.6). Ordered by evidence so the ones most likely to be worth approving
    are the ones somebody sees first.
    """
    stmt = (
        select(FetchPolicy)
        .where(
            FetchPolicy.seed_allowed.is_(None),
            FetchPolicy.first_seen_via.not_in(FRONTIER_DISCOVERY),
            FetchPolicy.first_seen_via.is_not(None),
        )
        .order_by(FetchPolicy.novel_fetches.desc(), FetchPolicy.domain)
    )
    return list((await sess.execute(stmt)).scalars().all())
