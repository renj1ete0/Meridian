"""Claiming and releasing queue tasks (spec §5.1, §6.1, §13.4).

The queue is the decoupling point between planes — the worker and the
orchestrator never call each other, they only leave rows here (§2 principle 2) —
so the claim has to be correct under concurrency without either side knowing the
other exists.

Three properties the implementation exists for:

**No task is claimed twice.** ``FOR UPDATE SKIP LOCKED`` does the work: a
competing claimer skips a locked row rather than blocking on it, so N workers
drain the queue in parallel without coordination and without a queue server.

**A dead worker does not strand its task.** Claiming takes a *lease* rather than
flipping status. A crashed worker leaves a claim that simply expires; nothing has
to notice the crash, which is the only design that survives "runs unattended for
weeks".

**A failing domain stops spinning the queue.** Failures set ``next_attempt_at``
to an exponentially backed-off time, so a dead site costs one attempt per backoff
window instead of one per loop iteration (§13.4).

**A refusal is not a failure to retry.** :func:`queue_disposition` decides which
of the two a fetch outcome is, so a robots denial is abandoned once rather than
re-asked three times over an hour to be told the same thing.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import random
from collections.abc import Collection, Sequence

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import robotscache
from .logging import get_logger
from .models import QueueTask
from .policy import BACKOFF_STATUS
from .tiering import registrable_domain
from .trust import record_discovery

log = get_logger(__name__)

DEFAULT_LEASE_SECONDS = 900  # 15 min — longer than any single fetch should take
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_BASE_S = 5


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def backoff_delay_s(
    attempts: int,
    base_s: int = DEFAULT_BACKOFF_BASE_S,
    max_s: int = 3600,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff with full jitter, capped.

    Jittered because synchronised retries are how a transient outage turns into
    a thundering herd the moment the domain recovers: without it every task that
    failed together also retries together. Full jitter (a draw from ``[0, d]``
    rather than ``d ± ε``) spreads them properly.
    """
    if attempts < 0:
        raise ValueError("attempts must not be negative")
    ceiling = min(base_s * (2**attempts), max_s)
    return (rng or random).uniform(0, ceiling)


#: Seed sources a crawl *followed* rather than chose (`B-61`).
FOLLOWED_SOURCES = ("frontier", "sitemap")

#: A directed claim skips work at or below this priority: a cited paper whose
#: citing page was off-topic sits here on purpose, and a reserved slot is not
#: the place to spend on it.
DIRECTED_FLOOR = 3


async def claim_next(
    sess: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    topics: list[str] | None = None,
    task_types: list[str] | None = None,
    directed: bool = False,
    skip_domains: Collection[str] = (),
) -> QueueTask | None:
    """Claim the highest-priority eligible task, or return None if there is none.

    ``skip_domains`` (`B-112`) leaves out page tasks on those domains or under
    them: the worker's busy hosts. Priority order alone sent every lane to the
    same few hosts at the top of the queue — one with a long crawl delay held
    the whole worker to its pace. Lookups and queries are never skipped; they
    do not wait on a host.

    ``directed`` narrows the claim to work somebody or something *chose* rather
    than followed (`B-61`): search results, queries, seeds and cited papers above
    the priority floor. Frontier and sitemap links are excluded. A worker spends
    a fixed share of its claims this way, because priority alone could not stop a
    large frontier from out-ranking every search result the crawl produced.

    Eligible means: pending, past its backoff time, and either unclaimed or
    holding a lease that has expired. Ordered by priority then age, so
    tier-upranked results (§5.2) are fetched first and nothing starves.

    ``task_types`` narrows the claim to the kinds the caller can actually
    handle. The queue holds ``query``, ``doi`` and ``sitemap`` tasks as well as
    ``url`` ones, and a claimer that takes a row it cannot process has only two
    ways out — fail a task that was never broken, or hand it back and claim it
    again on the next pass forever. Filtering in the query is the third.

    The row lock is held only for the duration of this statement — the claim is
    committed before any fetching starts, because holding a transaction open
    across a network fetch would pin a connection for the whole request.
    """
    now = _now()
    lease_cutoff = now - dt.timedelta(seconds=lease_seconds)

    stmt = (
        select(QueueTask)
        .where(
            QueueTask.status == "pending",
            or_(QueueTask.next_attempt_at.is_(None), QueueTask.next_attempt_at <= now),
            or_(QueueTask.claimed_at.is_(None), QueueTask.claimed_at < lease_cutoff),
        )
        .order_by(QueueTask.priority.desc(), QueueTask.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if topics:
        stmt = stmt.where(QueueTask.topic.in_(topics))
    if task_types:
        stmt = stmt.where(QueueTask.task_type.in_(task_types))
    if skip_domains:
        host = func.regexp_replace(
            func.lower(func.split_part(func.split_part(QueueTask.url_or_query, "://", 2), "/", 1)),
            r"^www\.|:\d+$",
            "",
            "g",
        )
        on_busy = or_(*[or_(host == d, host.like(f"%.{d}")) for d in sorted(set(skip_domains))])
        stmt = stmt.where(or_(QueueTask.task_type != "url", ~on_busy))
    if directed:
        stmt = stmt.where(
            or_(
                QueueTask.seed_source.not_in(FOLLOWED_SOURCES),
                QueueTask.task_type.in_(("query", "doi")),
            ),
            QueueTask.priority > DIRECTED_FLOOR,
        )

    task = (await sess.execute(stmt)).scalar_one_or_none()
    if task is None:
        return None

    task.claimed_at = now
    task.claimed_by = worker_id
    await sess.flush()
    return task


async def release(sess: AsyncSession, task: QueueTask) -> None:
    """Drop the lease without consuming an attempt.

    For shutdown, not for failure: a worker stopping cleanly should hand the task
    straight back rather than making it wait out a backoff it did not earn.
    """
    task.claimed_at = None
    task.claimed_by = None
    await sess.flush()


async def advance(sess: AsyncSession, task: QueueTask, status: str) -> None:
    """Move a task along the status flow and drop its lease."""
    task.status = status
    task.claimed_at = None
    task.claimed_by = None
    if status == "fetched":
        task.fetched_at = _now()
    await sess.flush()


async def fail(
    sess: AsyncSession,
    task: QueueTask,
    error: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base_s: int = DEFAULT_BACKOFF_BASE_S,
    floor_s: float = 0.0,
    rng: random.Random | None = None,
) -> bool:
    """Record a failure. Returns True if the task will be retried.

    Past ``max_retries`` the task is marked ``failed`` and left in place rather
    than deleted — the error text is the only record of why a URL never made it
    in, and §12.5's health line depends on being able to see it.

    ``floor_s`` holds the retry back at least that long, for a failure whose
    cause is cached (:func:`retry_floor_s`); the usual backoff comes on top.
    """
    task.attempts += 1
    task.error = error[:2000]
    task.claimed_at = None
    task.claimed_by = None

    if task.attempts > max_retries:
        task.status = "failed"
        task.next_attempt_at = None
        await sess.flush()
        return False

    # Added to the backoff, not max()ed with it: every task refused on one
    # origin would otherwise come back at the same instant, on the very edge of
    # the cache expiring — the synchronised herd the jitter exists to break up.
    delay = floor_s + backoff_delay_s(task.attempts, base_s=backoff_base_s, rng=rng)
    task.next_attempt_at = _now() + dt.timedelta(seconds=delay)
    await sess.flush()
    return True


async def reclaim_expired(sess: AsyncSession, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> int:
    """Clear leases held past their expiry. Returns how many were released.

    Not strictly required — :func:`claim_next` already ignores an expired lease —
    but running it on startup makes abandoned work visible in the queue rather
    than only implicit in a timestamp comparison.
    """
    cutoff = _now() - dt.timedelta(seconds=lease_seconds)
    result = await sess.execute(
        update(QueueTask)
        .where(QueueTask.claimed_at.is_not(None), QueueTask.claimed_at < cutoff)
        .values(claimed_at=None, claimed_by=None)
    )
    await sess.flush()
    return result.rowcount or 0


async def abandon(sess: AsyncSession, task: QueueTask, error: str) -> None:
    """Mark a task failed now, with no further attempts.

    Distinct from :func:`fail`, which spends a retry. Some outcomes are refusals
    rather than failures — robots.txt disallows the path, the domain is blocked,
    the media type is not on the allowlist — and asking again in five seconds
    gets the same answer for the same reason. The attempt is still counted, so
    ``queue.attempts`` stays an honest record of how many requests a URL cost.
    """
    task.attempts += 1
    task.error = error[:2000]
    task.status = "failed"
    task.next_attempt_at = None
    task.claimed_at = None
    task.claimed_by = None
    await sess.flush()


async def release_worker_claims(sess: AsyncSession, worker_id: str) -> int:
    """Drop every lease this worker still holds. Returns how many.

    Called on the way out of a clean shutdown. Without it, a restart cannot
    touch the tasks the previous process had claimed until their leases expire
    — fifteen minutes of a queue that looks busy and is doing nothing, on every
    deploy. Only ``pending`` rows are touched, so a task that finished and moved
    on is not reopened.
    """
    result = await sess.execute(
        update(QueueTask)
        .where(QueueTask.claimed_by == worker_id, QueueTask.status == "pending")
        .values(claimed_at=None, claimed_by=None)
    )
    await sess.flush()
    return result.rowcount or 0


async def queue_depth(sess: AsyncSession) -> dict[str, int]:
    """Count of tasks by status — the queue-depth half of §12.5's health line.

    Returned as counts per status rather than a single number: a queue of 4,000
    ``pending`` and one of 4,000 ``failed`` are the same depth and opposite
    situations, and the point of the health line is to tell them apart.
    """
    rows = await sess.execute(select(QueueTask.status, func.count()).group_by(QueueTask.status))
    return {status: count for status, count in rows.all()}


#: The fetch succeeded and there are bytes to extract: the task moves on.
TASK_FETCHED = frozenset({"success"})

#: The conditional request paid off. There is nothing new to extract — the
#: content is already in the corpus from the fetch that produced the validator —
#: so the task is finished rather than passed down a pipeline with no body to
#: work on.
TASK_UNCHANGED = frozenset({"not_modified"})

#: Nothing came back, but asking again later could plausibly change that.
TASK_RETRY = frozenset({"timeout", "connection_error", "robots_unreachable"})

#: The earliest a retry may come back, for outcomes whose cause is cached.
#: An unreadable robots.txt refuses its origin for ``ERROR_TTL_S``, and the
#: ordinary backoff (seconds) would spend every retry inside that window being
#: told the cached "no" — so the URL would fail permanently over one blip, which
#: is the `robots_denied` bug again with a different label.
RETRY_FLOOR_S: dict[str, float] = {"robots_unreachable": robotscache.ERROR_TTL_S}


def retry_floor_s(outcome: str) -> float:
    """The minimum retry delay this outcome needs; 0 for most."""
    return RETRY_FLOOR_S.get(outcome, 0.0)


#: A refusal, not a failure. Every member is deterministic in the retry window:
#: robots.txt and the block list will say the same thing in five seconds, the
#: page is still the size it is, the body still will not decompress, and the
#: address is still the address ``netguard`` refused. Retrying spends the
#: crawl's politeness budget to be told the same thing three times.
#:
#: ``too_many_redirects`` and ``decompression_bomb`` sit here while
#: :data:`~meridian_core.policy.DOMAIN_UNREACHABLE` counts them against the
#: domain — deliberately. "Should this domain be backed off" and "should this
#: URL be asked again" are different questions, and a hostile or misconfigured
#: response answers yes to the first and no to the second.
TASK_ABANDON = frozenset(
    {
        "robots_denied",
        "blocked",
        "unsafe_target",
        "content_type_rejected",
        "too_large",
        "decompression_bomb",
        "parse_error",
        "too_many_redirects",
    }
)


def queue_disposition(outcome: str, status_code: int | None = None) -> str:
    """What one fetch outcome means for the task: the next status, or how to end it.

    Returns ``"fetched"``, ``"done"``, ``"retry"`` or ``"abandon"``.

    The question here is not the one :func:`~meridian_core.policy.domain_signal`
    asks. That one decides whether a domain is worth continuing to fetch from;
    this one decides whether *this URL* is worth asking for again. A 404 is the
    domain working perfectly and the URL being permanently gone, and the two
    functions disagree about it for that reason.
    """
    if outcome in TASK_FETCHED:
        return "fetched"
    if outcome in TASK_UNCHANGED:
        return "done"
    if outcome in TASK_RETRY:
        return "retry"
    if outcome in TASK_ABANDON:
        return "abandon"
    if outcome == "http_error":
        # 5xx is the server having a bad minute and 429 is it asking for one;
        # both are worth a backed-off retry. Every other 4xx is a correct
        # answer about a URL that is not coming back.
        if status_code is None or status_code >= 500 or status_code == BACKOFF_STATUS:
            return "retry"
        return "abandon"
    # As in domain_signal: an outcome nobody classified must not take the worker
    # down at 3am. Retry is the forgiving default — it is bounded by
    # ``max_retries`` either way, where abandoning would silently drop the URL.
    log.warning("unclassified fetch outcome; retrying by default", extra={"outcome": outcome})
    return "retry"


async def enqueue(
    sess: AsyncSession,
    url: str,
    *,
    topic: str | None = None,
    seed_source: str = "frontier",
    task_type: str = "url",
    priority: int = 0,
    seed_mechanism: str | None = None,
    parent_source_id: int | None = None,
) -> QueueTask:
    """Add one task to the queue. Flushes; does not commit.

    ``seed_mechanism`` names which of §7.4's diversity mechanisms asked for a
    ``diversity`` query (`P5-05`); the column's CHECK refuses anything else.

    Deliberately does no filtering. Whether a URL is worth fetching is a
    question about blocklists, what has already been seen and what the domain's
    policy says — all of which need more than the queue table, and all of which
    belong to `worker/prefilter.py`. A queueing function that also decided
    policy would be impossible to test and impossible to reuse from Admin.

    It does *record* one thing, though (`P4-12`): how the domain first became
    known. That goes here rather than at the four call sites because it must not
    be forgettable — a call site added next year that skipped it would leave a
    domain with no provenance, and a domain with no provenance can never
    auto-approve. Recording is not deciding: `seed_allowed` is untouched except
    for an operator's own seed, which is consent.
    """
    with contextlib.suppress(ValueError):
        await record_discovery(sess, registrable_domain(url), seed_source=seed_source)

    task = QueueTask(
        url_or_query=url,
        topic=topic,
        seed_source=seed_source,
        task_type=task_type,
        priority=priority,
        seed_mechanism=seed_mechanism,
        parent_source_id=parent_source_id,
    )
    sess.add(task)
    await sess.flush()
    return task


async def already_queued(sess: AsyncSession, urls: Sequence[str]) -> set[str]:
    """Which of ``urls`` already have a queue row, in any status.

    Any status on purpose. A URL that failed is not worth immediately retrying
    under a different task id — that is what `next_attempt_at` is for — and one
    that is `done` is not worth re-fetching just because another page links to
    it. Re-crawl scheduling is a separate decision from frontier expansion, and
    conflating them would make every page's link list resurrect the whole corpus.
    """
    if not urls:
        return set()
    rows = await sess.execute(
        select(QueueTask.url_or_query).where(QueueTask.url_or_query.in_(list(urls)))
    )
    return set(rows.scalars())


async def enqueue_dois(
    sess: AsyncSession,
    dois: Sequence[str],
    *,
    topic: str | None,
    seed_source: str,
    priority: int,
    parent_source_id: int | None,
) -> int:
    """Queue DOIs one page named, each recording that page (`B-58`). Returns how many are new.

    ``dois`` must already be normalised: this compares them as strings, and two
    spellings of one DOI would be two rows, two resolutions and two fetches.

    A DOI already queued is not queued again, but a *pending* one ranked below
    ``priority`` is raised to it and takes this page as its parent. A paper a
    second page cites is worth what the better of the two is worth, and the
    first page to name it is often the weaker one — a listing or a tag page
    reaches a reference before the article that discusses it. A row that is not
    pending is left alone: it has been answered, and re-ranking an answer does
    nothing.
    """
    if not dois:
        return 0
    unique = list(dict.fromkeys(dois))
    known = await already_queued(sess, unique)
    if known:
        await sess.execute(
            update(QueueTask)
            .where(
                QueueTask.url_or_query.in_(list(known)),
                QueueTask.task_type == "doi",
                QueueTask.status == "pending",
                QueueTask.priority < priority,
            )
            .values(priority=priority, parent_source_id=parent_source_id)
        )
    fresh = [doi for doi in unique if doi not in known]
    for doi in fresh:
        await enqueue(
            sess,
            doi,
            topic=topic,
            seed_source=seed_source,
            task_type="doi",
            priority=priority,
            parent_source_id=parent_source_id,
        )
    return len(fresh)


async def record_search_yield(
    sess: AsyncSession, task_id: int, *, results: int, queued: int
) -> None:
    """Store what one answered query produced (`B-56`). Flushes; does not commit."""
    if results < 0 or queued < 0 or queued > results:
        raise ValueError("a search yield is two counts, queued never more than returned")
    await sess.execute(
        update(QueueTask)
        .where(QueueTask.task_id == task_id)
        .values(search_results=results, search_queued=queued)
    )
