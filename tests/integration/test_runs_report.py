"""The synthesis report to Telegram (task B-262).

A message per run would be one a minute while the daemon works, and a channel that noisy stops
being read, so the report covers every run since the last report and is silent when nothing
ended and nothing waits. Against a real Postgres: it is a query over `runs`, and the "since the
last report" mark is a row in `notifications`. Each test runs in a transaction rolled back at
the end, with the dev database's own waiting runs set aside inside it.
"""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select, update

from meridian_core.models import Notification, Run
from worker import digest
from worker.digest import REASONS_SHOWN, build_runs_report, run_runs_report

pytestmark = pytest.mark.usefixtures("require_db")

# Far from any real run, so the window holds only this file's rows.
SINCE = dt.datetime(2031, 3, 1, 0, 0, tzinfo=dt.UTC)
NOW = SINCE + dt.timedelta(hours=6)


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    await s.rollback()
    # Waiting runs are current state, not windowed: set the dev database's aside for the test.
    await s.execute(update(Run).where(Run.status == "deferred").values(status="failed"))
    await s.flush()
    yield s
    await s.rollback()


def a_run(sess, *, status="done", minutes=60, **fields) -> Run:
    run = Run(
        status=status,
        stage="done" if status == "done" else "extract",
        started_at=SINCE + dt.timedelta(minutes=minutes - 5),
        completed_at=None if status == "deferred" else SINCE + dt.timedelta(minutes=minutes),
        **fields,
    )
    sess.add(run)
    return run


async def test_nothing_ended_and_nothing_waiting_says_nothing(sess) -> None:
    text, payload = await build_runs_report(sess, since=SINCE, now=NOW)
    assert text is None
    assert payload["runs"] == 0


async def test_the_report_adds_up_what_the_runs_wrote_and_who_wrote_it(sess) -> None:
    a_run(sess, agent_id="local-llamacpp", edges_added=20, tags_added=2, tokens_used=13_000)
    a_run(sess, agent_id="local-llamacpp", edges_added=11, tags_added=0, tokens_used=16_000)
    a_run(sess, agent_id="hosted-mid", edges_added=4, tags_added=1, tokens_used=9_000, minutes=90)
    a_run(sess, status="failed", error="relation_extraction: every agent refused", minutes=120)
    # Ended outside the window: neither counted nor named.
    a_run(sess, edges_added=999, minutes=-60)
    await sess.flush()

    text, payload = await build_runs_report(sess, since=SINCE, now=NOW)

    assert (payload["done"], payload["failed"], payload["edges"], payload["tags"]) == (3, 1, 35, 3)
    assert "runs: 3 done, 1 failed" in text
    assert "wrote: 35 relations, 3 attributes" in text
    assert "local-llamacpp ×2" in text and "hosted-mid ×1" in text
    assert "every agent refused" in text
    assert "999" not in text
    assert text.splitlines()[0] == "Meridian synthesis — last 6h"


async def test_the_waiting_run_says_why_even_when_none_ended(sess) -> None:
    """ "The GPU machine is off" is the commonest answer to "why did nothing happen"."""
    run = a_run(
        sess, status="deferred", error="relation_extraction: local-llamacpp: ConnectTimeout"
    )
    await sess.flush()

    text, _ = await build_runs_report(sess, since=SINCE, now=NOW)

    assert text is not None, "a waiting run is worth a message even when none ended"
    assert f"waiting: run {run.run_id} at extract: relation_extraction: local-llamacpp" in text
    assert "runs: 0 done" in text


async def test_only_a_few_failures_are_named(sess) -> None:
    for n in range(REASONS_SHOWN + 2):
        a_run(sess, status="failed", error=f"failure number {n}", minutes=10 + n)
    await sess.flush()

    text, _ = await build_runs_report(sess, since=SINCE, now=NOW)

    assert sum(line.startswith("✗ ") for line in text.splitlines()) == REASONS_SHOWN


@pytest.fixture
def through_the_test_session(sess, monkeypatch):
    """`run_runs_report` opens its own session; give it the test's, commits as flushes."""

    @asynccontextmanager
    async def make():
        sess.commit = sess.flush
        yield sess

    async def nothing():
        return None

    monkeypatch.setattr(digest, "session", make)
    monkeypatch.setattr(digest, "dispose_engines", nothing)
    return sess


async def test_each_report_starts_where_the_last_stopped(
    through_the_test_session, monkeypatch
) -> None:
    sess = through_the_test_session
    sent: list[str] = []

    class Bot:
        async def send(self, text):
            sent.append(text)
            return True

        async def aclose(self):
            return None

    monkeypatch.setattr(digest.Telegram, "from_env", classmethod(lambda cls: Bot()))

    a_run(sess, agent_id="local-llamacpp", edges_added=5)
    await sess.flush()
    # A report made just before this window: the next one starts there, not 24 h back.
    sess.add(Notification(notification_type="run_summary", title="earlier", body="earlier"))
    await sess.flush()
    await sess.execute(
        update(Notification)
        .where(Notification.title == "earlier")
        .values(created_at=SINCE - dt.timedelta(minutes=1))
    )

    assert await run_runs_report(now=NOW) is True
    assert len(sent) == 1 and "5 relations" in sent[0]
    recorded = await sess.scalar(
        select(Notification)
        .where(Notification.notification_type == "run_summary")
        .order_by(Notification.notification_id.desc())
    )
    assert recorded.payload["since"] == (SINCE - dt.timedelta(minutes=1)).isoformat()
    assert recorded.surface == "admin"


async def test_the_daily_digest_says_growth_backlog_and_synthesis() -> None:
    """Read at a moment no row is near: the day is empty, and each line says so in words."""
    from meridian_core.db import dispose_engines

    try:
        lines = await digest._corpus_lines(now=dt.datetime(2040, 1, 1, tzinfo=dt.UTC))
    finally:
        await dispose_engines()

    assert lines[0] == "corpus: +0 sources kept, +0 searchable passages"
    assert lines[1].startswith("embedding: ")
    assert lines[2] == "synthesis: no run finished"
