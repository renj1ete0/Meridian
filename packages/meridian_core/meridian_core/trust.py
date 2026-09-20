"""What the crawl is willing to hand a model (task `P4-14`, §2.5, §11.8).

`P1-23` built the injection pre-screen and deliberately stopped at flagging:
its own text says "flags only; §2.5 keeps the page stored, extracted and
chunked". That was right — a screen that quarantines before anyone has seen its
false-positive rate quarantines the corpus. It has since run clean across every
real page crawled, so this is the half that acts on it.

**Screening is paid once per domain.** The verdict is cached on `fetch_policy`,
not recomputed per page. A site with four thousand pages must not be judged four
thousand times, and a domain cleared on Monday must not have page 3,001
quarantined on Friday because that page happened to quote an instruction.

**Quarantined content is stored, never deleted.** §2.5 is explicit. The page
keeps its raw file, its extraction and its chunks; what it loses is eligibility
for the set the slow loop reads. That is reversible, and deletion is not — and
the thing being screened for is a false positive away from ordinary writing
about security.

**`unscreened` is not `cleared`.** The filter admits `cleared` explicitly rather
than excluding `quarantined`, so a page nothing has examined does not reach a
model by default. "Not known to be bad" and "checked" are different claims and
the difference is the whole point of screening.

**Two ways a domain clears itself, both cheap.** A domain in the seeded tier map
is cleared on sight — somebody curated that list, which is exactly the human
judgement this would otherwise be asking a model for. Otherwise a domain clears
after `CLEAN_FETCHES_TO_CLEAR` consecutive unflagged fetches, and the counter
resets on any flag, the same shape as `consecutive_failures` and
`render_js_escalations`: a domain that starts serving hostile pages stops being
treated as though it had not.

What is *not* here is the part that needs a model: an unknown domain that trips
the screen is quarantined and left that way for a frontier model to judge
(`P4-07`). Queueing that judgement is phase 4. Until then a quarantine is
cleared by a person, which is a worse experience and the correct failure — the
alternative is admitting unscreened content because nothing was available to
screen it.
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
#:
#: Five rather than one, because a single clean page proves nothing about a
#: domain — the pages that carry an injection are rarely the first one linked.
#: Five rather than fifty, because until a domain clears, everything it serves
#: is held back from the slow loop, and a threshold nobody reaches is a corpus
#: nobody can read.
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

    The page is not simply given the domain's state. A flagged page on an
    unscreened domain is `quarantined` on its own account, because the domain
    having no verdict is not a reason to admit a page that tripped the screen.

    A flagged page on a *cleared* domain stays cleared, and that is the
    deliberate half: clearing a domain is a statement that its content is
    trusted, and re-quarantining individual pages afterwards would make the
    clearing meaningless while producing exactly the drip of false positives
    `P1-23` was careful to avoid.
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

    Returns the domain's state *after* this fetch, which is what the caller
    stores on the page. Called once per fetch, so it is deliberately cheap: one
    row, no history table — `trust_decided_at` and `trust_decided_by` carry
    enough to explain the current verdict, and the crawl's own logs carry the
    rest.

    A `rejected` domain is left alone. Rejection is a decision somebody made,
    and a crawl that un-rejected a domain by fetching five clean pages from it
    would be overruling them.
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
        # The counter resets whatever the current state is. A cleared domain
        # stays cleared — see `page_state` — but it starts earning its clearing
        # again, so a domain that has turned hostile does not keep a stale
        # streak behind it.
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

    Applied to the *chunk* query rather than at the source, because that is
    where every reader already starts and a filter one caller can forget to join
    is a filter that will be forgotten. Expressed as `IN (cleared)` rather than
    `!= quarantined` for the reason the module docstring gives: an unexamined
    page is not a cleared one.

    Deliberately **not** applied to the operator's own search. §2.5 keeps
    quarantined content stored and visible; what it withholds is content going
    to a model. Somebody reading their own corpus should see what was
    quarantined — that is how a false positive gets noticed.
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
#
# A third question beside "may we fetch this" (`status`) and "may a model read
# what came back" (`trust_state`). §11.4 caps what a model may seed; this is
# about *which domains* the cap applies within.

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

    Called wherever a URL is queued. The first `seed_source` sticks: a domain
    found by following a link and later proposed by a model was still found by
    following a link, and letting the later event win would erase the
    provenance that decides whether it may auto-approve.

    An operator's own seed is allowed immediately — typing a URL is consent,
    and making somebody wait three fetches for a domain they chose would be
    the system disbelieving them.
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

    Returns the domain's `seed_allowed` after the count. A domain whose first
    sighting was a model's proposal is **not** approved by this: it accrues the
    same evidence and still waits for a person, because the failure being
    avoided is a model talking the crawl into a domain by describing it
    confidently, and evidence gathered after the proposal is evidence the
    proposal caused.
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
