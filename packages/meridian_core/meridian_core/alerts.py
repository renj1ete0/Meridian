"""Sustained conditions, not events (task P5-07, spec §13.3, §12.5).

> "Alerts on *sustained* conditions only — fetch success below threshold for an
> hour, no successful run in 48h, disk above 80%, agent failures repeated.
> **Single-event alerting teaches me to ignore the channel, which is the real
> failure mode.**"

That last sentence is the design. A channel that cries about one timeout is a
channel nobody reads, and an unread channel is worse than no channel — it is the
appearance of monitoring without the fact of it. So every condition here is
measured over a window, and every alert is suppressed for a cooldown after it
fires.

**Suppression is in the database, not in the process.** The digest runs on a
timer and exits; anything it remembered in memory would be forgotten before the
next run, and the same alert would arrive every time the timer fired — which is
single-event alerting wearing a different hat. `notifications` already exists for
this, and using it means the in-app panel (`P6-08`) shows exactly what was sent.

**Everything is measured against the database or the disk.** No counters to keep
in step, no state that can drift from reality: "what was the fetch success rate
in the last hour" is a question `fetch_attempts` can already answer, and a rate
derived from the rows is a rate that cannot lie about them.
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
from .models import FetchAttempt, Notification, QueueTask

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

    The failure that cost `v0.26.0`: a crawl that drains its queue and idles
    logs exactly what a healthy one logs, and reports the same numbers. Nothing
    else here would notice, because there are no failures — there is simply no
    work, and an unattended run keeps not doing it for two days.
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

    Recorded whether or not a channel is configured, because the in-app panel
    (`P6-08`) and the Telegram digest are two views of one thing — and a
    deployment with no bot token should still be able to see what would have
    been sent.
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
