"""What a long crawl has been doing, and whether it still is (task P6-25, spec §12.5, §13.4).

`/progress` answers "is anything happening" in one line, which is the right
question for a fresh install's first hour. An unattended run of days asks a
different one: *when did it stop, and why*. That needs a day of history rather
than an hour, the outcome mix rather than a single rate, the queue and the
embedding backlog side by side, and a verdict on liveness that says so in words.

**The failure this exists for is silence.** §13.4's unattended system does not
crash in the way that matters; it stops fetching while every process stays up,
or it drains its frontier and idles, and both of those log exactly what a
healthy crawl logs. `alerts` catches them hours later, on a window chosen to
stay quiet. This is the screen somebody looks at in between.

**Everything is as of one instant.** ``now`` bounds every window and the last
attempt, and the verdict counts only queue rows that existed at ``now``. In
production that is the present and the bounds cost nothing; in tests it is what
lets a verdict be asserted against a database that holds a real crawl, by
choosing an instant no real row is near.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from sqlalchemy import DateTime, Integer, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .attempts import SUCCESS_OUTCOMES
from .chunks import embedding_backlog
from .models import FetchAttempt, QueueTask
from .models.queue import FETCH_OUTCOME, TASK_STATUS
from .queueing import DEFAULT_LEASE_SECONDS, queue_depth
from .schemas.enums import LivenessState

#: The history the chart covers, in hourly buckets.
HOURS = 24

#: How many domains the last-hour list names.
TOP_DOMAINS = 10

#: No fetch attempt for this long, with work that is ready, is a stall.
#:
#: The claim lease, because it is already this codebase's statement of "longer
#: than any single fetch should take". A gap shorter than one lease can be one
#: slow render plus a politeness delay; a gap longer than one lease means no
#: fetch has *finished* in the time any fetch is allowed, including a worker
#: that died holding a claim and whose lease has only just expired. Any shorter
#: and a slow site reads as an outage; much longer and a dead worker costs an
#: afternoon before the screen admits it.
STALL_AFTER = dt.timedelta(seconds=DEFAULT_LEASE_SECONDS)


@dataclasses.dataclass(frozen=True)
class HourBucket:
    """One hour of fetch attempts, split by whether they got what they asked for.

    Success is ``SUCCESS_OUTCOMES``, so a 304 counts — the same line `alerts`
    draws, because a chart and an alert that disagreed about what a success is
    would each make the other look wrong.
    """

    start: dt.datetime
    succeeded: int
    failed: int


@dataclasses.dataclass(frozen=True)
class OutcomeCount:
    outcome: str
    count: int


@dataclasses.dataclass(frozen=True)
class DomainCount:
    domain: str
    attempts: int
    succeeded: int


@dataclasses.dataclass(frozen=True)
class Liveness:
    """The verdict and the numbers it rests on.

    The numbers ride along so the screen can say *how* stalled — "no fetch in
    40 minutes with 3,000 ready" and "no fetch in 3 days with 2 ready" are the
    same state and very different mornings.
    """

    state: LivenessState
    last_attempt_at: dt.datetime | None
    #: Seconds since ``last_attempt_at``; None when nothing was ever attempted.
    quiet_seconds: int | None
    #: Pending rows whose backoff has passed: what a worker could claim now.
    ready: int
    #: All pending rows, including those still backing off.
    pending: int


@dataclasses.dataclass(frozen=True)
class CrawlHealth:
    as_of: dt.datetime
    stall_after_seconds: int
    #: Oldest first, exactly ``HOURS`` of them, empty hours included — a gap in
    #: the chart is the thing being looked for, so it must not be elided.
    hours: list[HourBucket]
    #: Every value in ``FETCH_OUTCOME``, zeros included, most frequent first.
    outcomes: list[OutcomeCount]
    #: Every value in ``TASK_STATUS``, zeros included.
    queue: dict[str, int]
    embedding_backlog: int
    top_domains: list[DomainCount]
    liveness: Liveness


def judge(
    *,
    last_attempt_at: dt.datetime | None,
    ready: int,
    pending: int,
    now: dt.datetime,
    stall_after: dt.timedelta = STALL_AFTER,
) -> LivenessState:
    """The verdict, from the numbers alone.

    Separate from the queries so every branch can be driven directly: the
    database a test runs against holds a real crawl, and "the queue is empty"
    is not a state a shared database can be put into.

    Order matters. A recent fetch is ``crawling`` even with an empty queue —
    the last few rows are still being worked — and emptiness is only reported
    once the fetching has actually stopped.
    """
    if last_attempt_at is not None and now - last_attempt_at <= stall_after:
        return "crawling"
    if ready > 0:
        # Includes a crawl that has never fetched anything: ready work and no
        # attempt on record is a worker that is not running.
        return "stalled"
    if pending > 0:
        return "waiting"
    return "idle"


async def crawl_health(sess: AsyncSession, *, now: dt.datetime | None = None) -> CrawlHealth:
    """Everything the dashboard shows, as of ``now``. Reads only."""
    now = now or dt.datetime.now(dt.UTC)
    at = literal(now, DateTime(timezone=True))
    day_ago = now - dt.timedelta(hours=HOURS)
    hour_ago = now - dt.timedelta(hours=1)
    succeeded = FetchAttempt.outcome.in_(list(SUCCESS_OUTCOMES))
    in_day = (FetchAttempt.attempted_at > day_ago, FetchAttempt.attempted_at <= now)

    # Rolling hours ending at `now`, not clock hours. A clock-aligned chart
    # always ends in a partial hour, and a partial hour is a short bar — which
    # on a screen whose purpose is spotting a crawl that slowed down is a false
    # alarm every time it is opened.
    hours_ago = func.floor(func.extract("epoch", at - FetchAttempt.attempted_at) / 3600).cast(
        Integer
    )
    per_hour = (
        await sess.execute(
            select(hours_ago, succeeded, func.count()).where(*in_day).group_by(hours_ago, succeeded)
        )
    ).all()
    tally = {(ago, ok): count for ago, ok, count in per_hour}
    hours = [
        HourBucket(
            start=now - dt.timedelta(hours=ago + 1),
            succeeded=tally.get((ago, True), 0),
            failed=tally.get((ago, False), 0),
        )
        for ago in range(HOURS - 1, -1, -1)
    ]

    by_outcome = dict(
        (
            await sess.execute(
                select(FetchAttempt.outcome, func.count())
                .where(*in_day)
                .group_by(FetchAttempt.outcome)
            )
        ).all()
    )
    outcomes = sorted(
        (OutcomeCount(outcome=o, count=by_outcome.get(o, 0)) for o in FETCH_OUTCOME.enums),
        key=lambda row: -row.count,
    )

    top = (
        await sess.execute(
            select(
                FetchAttempt.domain,
                func.count().label("attempts"),
                func.count(case((succeeded, 1))),
            )
            .where(FetchAttempt.attempted_at > hour_ago, FetchAttempt.attempted_at <= now)
            .group_by(FetchAttempt.domain)
            .order_by(func.count().desc(), FetchAttempt.domain)
            .limit(TOP_DOMAINS)
        )
    ).all()

    last = await sess.scalar(
        select(func.max(FetchAttempt.attempted_at)).where(FetchAttempt.attempted_at <= now)
    )
    pending_rows = (QueueTask.status == "pending", QueueTask.created_at <= now)
    pending = int(
        await sess.scalar(select(func.count()).select_from(QueueTask).where(*pending_rows)) or 0
    )
    # The claim query's eligibility, minus the lease: a claimed row is work in
    # flight, and in-flight work that produces no attempt for a whole lease is
    # the stall this is looking for.
    ready = int(
        await sess.scalar(
            select(func.count())
            .select_from(QueueTask)
            .where(
                *pending_rows,
                or_(QueueTask.next_attempt_at.is_(None), QueueTask.next_attempt_at <= now),
            )
        )
        or 0
    )

    depth = await queue_depth(sess)
    return CrawlHealth(
        as_of=now,
        stall_after_seconds=int(STALL_AFTER.total_seconds()),
        hours=hours,
        outcomes=outcomes,
        queue={status: depth.get(status, 0) for status in TASK_STATUS.enums},
        embedding_backlog=int(await embedding_backlog(sess)),
        top_domains=[DomainCount(domain=d, attempts=a, succeeded=s) for d, a, s in top],
        liveness=Liveness(
            state=judge(last_attempt_at=last, ready=ready, pending=pending, now=now),
            last_attempt_at=last,
            quiet_seconds=None if last is None else int((now - last).total_seconds()),
            ready=ready,
            pending=pending,
        ),
    )
