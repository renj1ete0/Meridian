"""Sustained conditions, not events (task P5-07, spec §13.3, §12.5).

Every condition is measured over a window from rows or the disk, and every alert is
suppressed for a cooldown, recorded in `notifications` rather than in memory.
See docs/features/operations.md#alerts-and-the-digest.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import shutil
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .attempts import SUCCESS_OUTCOMES
from .logging import get_logger
from .models import Chunk, FetchAttempt, Notification, QueueTask

log = get_logger(__name__)

#: How long an alert stays quiet after firing. Long enough that a condition
#: lasting all night produces one message rather than a column of them.
DEFAULT_COOLDOWN_HOURS = 6

#: Fetch success below this, sustained, is worth waking someone for.
MIN_SUCCESS_RATE = 0.5

#: Too few attempts to judge. A 0% success rate over three attempts is noise;
#: over three hundred it is an outage.
MIN_ATTEMPTS_TO_JUDGE = 20

#: §13.3's number.
DISK_WARN_FRACTION = 0.8

#: Chunks waiting for a vector before the backlog is worth reporting (`B-22`): about
#: an hour's catch-up on a CPU-only box.
EMBED_BACKLOG_LIMIT = 5_000

#: Fraction of the corpus that may be waiting before it is reported regardless
#: of the absolute count, so a small corpus mostly unembedded is caught.
EMBED_BACKLOG_FRACTION = 0.5


@dataclasses.dataclass(frozen=True)
class Alert:
    """One condition that has been true for long enough to say so."""

    #: Stable across firings, because it is what suppression matches on. A key
    #: that embedded the current value would never match its predecessor and
    #: every check would alert again.
    key: str
    title: str
    body: str


async def check_fetch_success(
    sess: AsyncSession, *, hours: int = 1, now: dt.datetime | None = None
) -> Alert | None:
    """Fetch success below threshold, over a window rather than at an instant."""
    cutoff = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(hours=hours)
    rows = (
        await sess.execute(
            select(FetchAttempt.outcome, func.count())
            .where(FetchAttempt.attempted_at >= cutoff)
            .group_by(FetchAttempt.outcome)
        )
    ).all()

    total = sum(count for _, count in rows)
    if total < MIN_ATTEMPTS_TO_JUDGE:
        # Not enough to judge, and saying so is different from saying it is
        # fine. A quiet crawl is caught by `check_queue_drained` instead.
        return None

    succeeded = sum(count for outcome, count in rows if outcome in SUCCESS_OUTCOMES)
    rate = succeeded / total
    if rate >= MIN_SUCCESS_RATE:
        return None

    # The breakdown, not just the rate. §12.5's reasoning: a run that is 40%
    # `robots_denied` needs the frontier looked at, and one that is 40%
    # `timeout` needs the network, and the rate alone cannot tell them apart.
    worst = sorted(((c, o) for o, c in rows if o not in SUCCESS_OUTCOMES), reverse=True)[:3]
    breakdown = ", ".join(f"{outcome} {count}" for count, outcome in worst)
    return Alert(
        key="fetch_success_low",
        title=f"Fetch success {rate:.0%} over {hours}h",
        body=f"{succeeded} of {total} attempts succeeded. Mostly: {breakdown}.",
    )


async def check_no_recent_success(
    sess: AsyncSession, *, hours: int = 6, now: dt.datetime | None = None
) -> Alert | None:
    """Nothing has been fetched successfully for a while.

    Distinct from a low rate: a worker that died, or a queue that drained, has
    no failures to lower a rate with. The silence is the signal.
    """
    moment = now or dt.datetime.now(dt.UTC)
    last = await sess.scalar(
        select(func.max(FetchAttempt.attempted_at)).where(
            FetchAttempt.outcome.in_(list(SUCCESS_OUTCOMES))
        )
    )
    if last is None or last >= moment - dt.timedelta(hours=hours):
        return None

    quiet = moment - last
    return Alert(
        key="no_recent_success",
        title=f"Nothing fetched for {quiet.total_seconds() / 3600:.0f}h",
        body=(
            f"The last successful fetch was {last.isoformat(timespec='minutes')}. "
            "Check the worker is running and that the queue has pending rows."
        ),
    )


async def check_queue_drained(sess: AsyncSession) -> Alert | None:
    """The frontier has nothing left to fetch.

    A drained crawl logs what a healthy one does (`v0.26.0`).
    """
    pending = await sess.scalar(
        select(func.count()).select_from(QueueTask).where(QueueTask.status == "pending")
    )
    if pending:
        return None

    return Alert(
        key="queue_drained",
        title="The frontier is empty",
        body=(
            "No pending queue rows. The crawl will idle rather than stop, and will "
            "look healthy while doing it. Seed more, or check that sitemap, search "
            "and citation expansion are enqueueing."
        ),
    )


def check_disk(path: str | Path, *, fraction: float = DISK_WARN_FRACTION) -> Alert | None:
    """§13.3's disk threshold, measured where the corpus actually lands."""
    try:
        usage = shutil.disk_usage(Path(path))
    except OSError:
        # A raw root that does not exist yet is not a disk problem, and an alert
        # saying it is would send somebody looking at the wrong thing.
        return None

    used = 1 - (usage.free / usage.total)
    if used < fraction:
        return None

    return Alert(
        key="disk_low",
        title=f"Disk {used:.0%} full",
        body=(
            f"{usage.free / 1e9:.1f} GB free at {path}. Nothing deletes from the raw "
            "store on its own — `python -m worker.sweep` reports what could go."
        ),
    )


async def recently_alerted(
    sess: AsyncSession,
    key: str,
    *,
    cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
    now: dt.datetime | None = None,
) -> bool:
    """Whether this condition has already been reported lately."""
    cutoff = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(hours=cooldown_hours)
    found = await sess.scalar(
        select(Notification.notification_id)
        .where(
            Notification.notification_type == "alert",
            Notification.created_at >= cutoff,
            Notification.payload["condition"].astext == key,
        )
        .limit(1)
    )
    return found is not None


async def record_alert(sess: AsyncSession, alert: Alert) -> Notification:
    """Write the alert down. Flushes; does not commit.

    Recorded whether or not a channel is configured; the in-app panel (`P6-08`) reads
    the same rows.
    """
    row = Notification(
        notification_type="alert",
        title=alert.title,
        body=alert.body,
        payload={"condition": alert.key},
        surface="admin",
    )
    sess.add(row)
    await sess.flush()
    log.warning("alert raised", extra={"condition": alert.key, "title": alert.title})
    return row


async def check_embedding_backlog(
    sess: AsyncSession,
    *,
    limit: int = EMBED_BACKLOG_LIMIT,
    fraction: float = EMBED_BACKLOG_FRACTION,
) -> Alert | None:
    """Vectors are falling behind the crawl (`B-22`, §6.1, §12.5).

    Fires on an absolute count or a share of the corpus waiting. Search does not
    report a partly covered vector arm. See docs/features/operations.md#alerts-and-the-digest.
    """
    waiting = int(
        await sess.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.embedding.is_(None), Chunk.superseded_at.is_(None))
        )
        or 0
    )
    total = int(await sess.scalar(select(func.count()).select_from(Chunk)) or 0)
    if not total or not waiting:
        return None

    share = waiting / total
    if waiting < limit and share < fraction:
        return None

    return Alert(
        key="embedding_backlog",
        title=f"{waiting:,} chunks are waiting for a vector",
        body=(
            f"{waiting:,} of {total:,} chunks ({share:.0%}) have no embedding. "
            "Search is running on its lexical arm alone for those, and reports "
            "nothing about it — a thin-looking corpus is the symptom. Check that "
            "the `embed` service is up and keeping pace; if the crawl is simply "
            "faster than the embedder, MERIDIAN_WORKER_CONCURRENCY is the brake."
        ),
    )


async def due_alerts(
    sess: AsyncSession,
    *,
    raw_root: str | Path | None = None,
    cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
    now: dt.datetime | None = None,
) -> list[Alert]:
    """Every condition that is true and has not been reported lately."""
    found = [
        await check_fetch_success(sess, now=now),
        await check_no_recent_success(sess, now=now),
        await check_queue_drained(sess),
        await check_embedding_backlog(sess),
        check_disk(raw_root) if raw_root else None,
    ]
    due = []
    for alert in found:
        if alert is None:
            continue
        if await recently_alerted(sess, alert.key, cooldown_hours=cooldown_hours, now=now):
            continue
        due.append(alert)
    return due
