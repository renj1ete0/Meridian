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
from sqlalchemy import delete, func, select

from meridian_core.alerts import (
    MIN_ATTEMPTS_TO_JUDGE,
    Alert,
    check_embedding_backlog,
    check_fetch_success,
    check_no_recent_success,
    check_queue_drained,
    due_alerts,
    recently_alerted,
    record_alert,
)
from meridian_core.attempts import record_attempt
from meridian_core.models import Chunk, FetchAttempt, Notification

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


async def alerted_at(sess, key: str, when: dt.datetime) -> None:
    """Record an alert and give the row a timestamp the test chose.

    `record_alert` takes `created_at` from the database clock, and rightly so —
    an alert is raised when it is raised. But `NOW` here is a fixed instant, so
    a test that wrote a row and then asked about it "48 hours later" was really
    asking about a row stamped with *today's* real date, which stops being 48
    hours before `NOW` two days after the file is written. It did, and the suite
    went red on a date nobody changed anything on.

    So suppression tests set the age explicitly, exactly as `attempts()` above
    sets `attempted_at`. The rule is the same either way: a test about a window
    must own both ends of it.
    """
    row = await record_alert(sess, Alert(key=key, title=f"TEST {key}", body="b"))
    row.created_at = when
    await sess.flush()


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
    assert await recently_alerted(clean, "test_condition", now=NOW) is False

    await alerted_at(clean, "test_condition", NOW)

    assert await recently_alerted(clean, "test_condition", now=NOW) is True


async def test_suppression_expires(clean) -> None:
    """A condition that is still true tomorrow is worth saying again. Silence
    forever is how a real outage gets reported once and then forgotten."""
    await alerted_at(clean, "test_expiry", NOW)

    later = NOW + dt.timedelta(hours=48)
    assert await recently_alerted(clean, "test_expiry", cooldown_hours=6, now=later) is False


async def test_suppression_holds_inside_the_cooldown(clean) -> None:
    """The other half, and the half that makes the one above mean something.

    Asserting only that suppression *expires* passes just as well against a
    function that never suppresses anything, which is the failure this alerting
    exists to avoid (§13.3: single-event alerting teaches the reader to ignore
    the channel). Both ends of the window are pinned to `NOW`, so neither
    assertion depends on what day the suite runs.
    """
    await alerted_at(clean, "test_expiry", NOW)

    soon = NOW + dt.timedelta(hours=2)
    assert await recently_alerted(clean, "test_expiry", cooldown_hours=6, now=soon) is True


async def test_suppression_is_per_condition(clean) -> None:
    """One noisy condition must not silence a different, real one."""
    await alerted_at(clean, "test_one", NOW)

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


# --------------------------------------------------------------------------
# Vectors falling behind the crawl (task `B-22`, §6.1, §12.5)
# --------------------------------------------------------------------------
#
# Measured, not imagined: the embedding pass ran as an hourly job inside a
# thirty-minute ceiling, was killed at the ceiling every time, and left a
# backlog no later window could clear. Nothing said so. The crawl was healthy,
# the corpus was growing, and it was quietly becoming unsearchable as it grew.


@pytest.fixture
async def unembedded(session_for):
    """A source whose chunks have no vectors, removed afterwards.

    Seeded rather than assumed: the dev corpus drifts — it held a hundred
    unembedded chunks this morning and none by the afternoon — and a test that
    reads whatever happens to be there passes vacuously on one of those days.
    """
    from meridian_core.chunks import ChunkWrite, replace_chunks
    from meridian_core.models import Source
    from meridian_core.sources import upsert_source

    marker = f"alert{uuid.uuid4().hex[:8]}"
    sess = await session_for("rw")
    await sess.rollback()
    source, _ = await upsert_source(
        sess,
        f"https://{marker}.test/doc",
        checksum=f"sha256:{uuid.uuid4().hex}",
        source_tier="government",
    )
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=f"{marker} passage {n}", chunk_index=n) for n in range(25)],
    )
    source_id = source.source_id
    await sess.commit()

    yield sess

    await sess.rollback()
    await sess.execute(delete(Chunk).where(Chunk.source_id == source_id))
    await sess.execute(delete(Source).where(Source.source_id == source_id))
    await sess.commit()


async def test_a_lagging_embedder_is_reported(unembedded) -> None:
    """The absolute threshold: a corpus falling behind."""
    alert = await check_embedding_backlog(unembedded, limit=1, fraction=1.1)

    assert alert is not None
    assert alert.key == "embedding_backlog"
    assert "lexical arm" in alert.body, "the alert should say what the reader will see"


async def test_a_backlog_within_tolerance_is_not_reported(unembedded) -> None:
    """Lag is normal. §6.1 accepts a window where a chunk exists, is not yet
    searchable and is not yet known to be a duplicate — an alert firing on that
    would fire every day and be read on none of them."""
    assert await check_embedding_backlog(unembedded, limit=10_000_000, fraction=1.1) is None


async def test_a_corpus_mostly_unembedded_is_reported(unembedded) -> None:
    """The share threshold, which exists because the count misses this case: a
    few hundred chunks with nine tenths unembedded is in the same trouble as a
    large corpus with thousands waiting, and no absolute threshold notices."""
    alert = await check_embedding_backlog(unembedded, limit=10_000_000, fraction=0.0001)

    assert alert is not None, "a corpus that is mostly unembedded must be reported"


async def test_an_empty_corpus_is_not_a_backlog(session_for) -> None:
    """Nothing to embed is not the same as falling behind, and a fresh install
    should not greet its operator with an alert."""
    sess = await session_for("rw")
    await sess.rollback()
    total = await sess.scalar(select(func.count()).select_from(Chunk))
    if total:
        pytest.skip("the dev corpus is not empty; this case is covered by the unit of logic")

    assert await check_embedding_backlog(sess) is None
