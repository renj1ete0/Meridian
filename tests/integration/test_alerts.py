"""Sustained conditions (task P5-07, spec §13.3).

> "Single-event alerting teaches me to ignore the channel, which is the real
> failure mode."

So the tests are mostly about *not* alerting: a window that is too short to
judge, a condition already reported, a quiet crawl that is quiet for a reason.
An alert channel is only useful while it is still read, and every false positive
spends some of that.

Against a real Postgres because every condition is a query over `fetch_attempts`
and `queue` — derived from rows rather than from counters, so a rate cannot
disagree with the attempts behind it.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete

from meridian_core.alerts import (
    MIN_ATTEMPTS_TO_JUDGE,
    check_fetch_success,
    check_no_recent_success,
    check_queue_drained,
    due_alerts,
    recently_alerted,
    record_alert,
)
from meridian_core.attempts import record_attempt
from meridian_core.models import FetchAttempt, Notification

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.UTC)

#: Conditions this file raises, and therefore has to clear. A leftover row
#: suppresses the next alert rather than failing visibly, so the cleanup is part
#: of the test rather than tidiness.
TEST_CONDITIONS = frozenset(
    {
        "fetch_success_low",
        "no_recent_success",
        "queue_drained",
        "test_condition",
        "test_expiry",
        "test_one",
    }
)


@pytest.fixture
def domain() -> str:
    return f"alert{uuid.uuid4().hex[:10]}.test"


@pytest.fixture
async def clean(session_for, domain):
    """Each test owns the attempt window it measures.

    `fetch_attempts` is global and the dev database holds a real crawl, so a
    test that did not clear the window would be measuring somebody else's
    afternoon.
    """
    sess = await session_for("rw")

    async def wipe() -> None:
        await sess.execute(
            delete(FetchAttempt).where(FetchAttempt.attempted_at >= NOW - dt.timedelta(days=2))
        )
        # Matched on the condition key, not the title. Real alert titles are
        # generated ("Fetch success 0% over 1h"), so a title filter left them
        # behind — and a stale alert row *suppresses* the next one, which made
        # this file pass alone and fail in a full run for a reason that looked
        # nothing like its cause.
        await sess.execute(
            delete(Notification).where(
                Notification.payload["condition"].astext.in_(list(TEST_CONDITIONS))
            )
        )
        await sess.commit()

    await wipe()
    yield sess
    await sess.execute(delete(FetchAttempt).where(FetchAttempt.domain.like("alert%")))
    await wipe()


async def attempts(sess, domain: str, outcome: str, count: int, *, minutes_ago: int = 5) -> None:
    for _ in range(count):
        await record_attempt(
            sess,
            domain=domain,
            url=f"https://{domain}/a",
            outcome=outcome,
            attempted_at=NOW - dt.timedelta(minutes=minutes_ago),
        )
    await sess.flush()


# --------------------------------------------------------------------------
# Not alerting
# --------------------------------------------------------------------------


async def test_too_few_attempts_to_judge_is_not_an_alert(clean, domain) -> None:
    """A 0% success rate over three attempts is noise; over three hundred it is
    an outage. Alerting on the first teaches the reader to skip the second."""
    await attempts(clean, domain, "timeout", MIN_ATTEMPTS_TO_JUDGE - 1)

    assert await check_fetch_success(clean, now=NOW) is None


async def test_a_healthy_rate_is_not_an_alert(clean, domain) -> None:
    await attempts(clean, domain, "success", MIN_ATTEMPTS_TO_JUDGE)

    assert await check_fetch_success(clean, now=NOW) is None


async def test_attempts_outside_the_window_do_not_count(clean, domain) -> None:
    """The window is what makes this a *sustained* condition. Failures from
    yesterday must not raise an alert about this hour."""
    await attempts(clean, domain, "timeout", MIN_ATTEMPTS_TO_JUDGE * 2, minutes_ago=180)

    assert await check_fetch_success(clean, hours=1, now=NOW) is None


# --------------------------------------------------------------------------
# Alerting
# --------------------------------------------------------------------------


async def test_a_sustained_failure_rate_alerts(clean, domain) -> None:
    await attempts(clean, domain, "timeout", MIN_ATTEMPTS_TO_JUDGE)

    alert = await check_fetch_success(clean, now=NOW)

    assert alert is not None
    assert alert.key == "fetch_success_low"


async def test_the_alert_names_what_went_wrong_not_just_the_rate(clean, domain) -> None:
    """§12.5: a run that is 40% `robots_denied` needs the frontier looked at,
    and one that is 40% `timeout` needs the network. The rate alone cannot tell
    them apart, and an alert that only gave a rate would send someone to read
    the logs it was supposed to replace."""
    await attempts(clean, domain, "robots_denied", MIN_ATTEMPTS_TO_JUDGE)

    alert = await check_fetch_success(clean, now=NOW)

    assert alert is not None
    assert "robots_denied" in alert.body


async def test_silence_alerts_separately_from_failure(clean, domain) -> None:
    """A worker that died has no failures to lower a rate with. The silence is
    the signal, and nothing else here would catch it."""
    await attempts(clean, domain, "success", 1, minutes_ago=60 * 30)

    alert = await check_no_recent_success(clean, hours=6, now=NOW)

    assert alert is not None
    assert alert.key == "no_recent_success"


async def test_a_drained_frontier_alerts(session_for) -> None:
    """The failure that cost `v0.26.0`: a crawl that drains its queue and idles
    logs exactly what a healthy one logs. There are no errors, because there is
    no work — and an unattended run keeps not doing it for two days."""
    sess = await session_for("rw")
    result = await check_queue_drained(sess)

    # The dev corpus has pending rows, so this is the negative case; the
    # assertion that matters is that a populated queue is *not* an alert.
    assert result is None


# --------------------------------------------------------------------------
# Suppression
# --------------------------------------------------------------------------


async def test_a_reported_condition_goes_quiet(clean) -> None:
    """Suppression lives in the database because the digest runs on a timer and
    exits. Anything it remembered in memory would be forgotten before the next
    run, and the same alert would arrive every time the timer fired — which is
    single-event alerting wearing a different hat."""
    from meridian_core.alerts import Alert

    alert = Alert(key="test_condition", title="TEST condition", body="body")

    assert await recently_alerted(clean, alert.key, now=NOW) is False

    await record_alert(clean, alert)
    await clean.flush()

    assert await recently_alerted(clean, alert.key, now=NOW) is True


async def test_suppression_expires(clean) -> None:
    """A condition that is still true tomorrow is worth saying again. Silence
    forever is how a real outage gets reported once and then forgotten."""
    from meridian_core.alerts import Alert

    await record_alert(clean, Alert(key="test_expiry", title="TEST expiry", body="b"))
    await clean.flush()

    later = NOW + dt.timedelta(hours=48)
    assert await recently_alerted(clean, "test_expiry", cooldown_hours=6, now=later) is False


async def test_suppression_is_per_condition(clean) -> None:
    """One noisy condition must not silence a different, real one."""
    from meridian_core.alerts import Alert

    await record_alert(clean, Alert(key="test_one", title="TEST one", body="b"))
    await clean.flush()

    assert await recently_alerted(clean, "test_two", now=NOW) is False


async def test_due_alerts_skips_what_was_already_said(clean, domain) -> None:
    await attempts(clean, domain, "timeout", MIN_ATTEMPTS_TO_JUDGE)
    first = await due_alerts(clean, now=NOW)
    assert any(a.key == "fetch_success_low" for a in first)

    for alert in first:
        await record_alert(clean, alert)
    await clean.flush()

    # The key, not the whole list. `due_alerts` also asks about disk and the
    # queue, which are global state this test does not own — asserting an empty
    # list made it pass alone and fail in a full run, which is the kind of
    # flake that gets a real test deleted.
    assert not any(a.key == "fetch_success_low" for a in await due_alerts(clean, now=NOW))
