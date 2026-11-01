"""Followed links wait for their page's verdict (task B-242).

The rule's pure parts: reading the wait from the policy's ``frontier`` block, refusing
what would quietly turn it off, refusing a wait on a row nobody followed, and the claim
order that keeps waiting links as the last resort, never as idle time. The database
half is in `tests/integration/test_worker_run.py` and `test_requeue_links.py`.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from sqlalchemy.dialects import postgresql

from meridian_core import queueing
from meridian_core.queueing import HELD_SOURCES, enqueue, held, ready
from worker.main import DEFAULT_VERDICT_WAIT_S, Worker, WorkerSettings, verdict_wait_from

NOW = dt.datetime(2026, 11, 2, 12, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("block", "seconds"),
    [
        ({}, DEFAULT_VERDICT_WAIT_S),
        ({"blocked_domains": ["example.invalid"]}, DEFAULT_VERDICT_WAIT_S),
        ({"verdict_wait_hours": 0}, 0.0),
        ({"verdict_wait_hours": 2}, 7200.0),
        ({"verdict_wait_hours": 0.5}, 1800.0),
    ],
)
def test_the_wait_is_read_from_the_frontier_block(block, seconds) -> None:
    assert verdict_wait_from(block) == seconds


@pytest.mark.parametrize("raw", [-1, "3", True, [3], {"hours": 3}])
def test_a_wait_that_is_not_hours_is_refused_not_read_as_off(raw) -> None:
    with pytest.raises(ValueError, match="verdict_wait_hours"):
        verdict_wait_from({"verdict_wait_hours": raw})


def test_the_default_waits_at_least_one_labelling_and_one_reranking_pass() -> None:
    """Both run hourly; a wait shorter than two hours ends before most verdicts land."""
    assert DEFAULT_VERDICT_WAIT_S >= 2 * 3600


def test_a_worker_refuses_a_negative_wait() -> None:
    with pytest.raises(ValueError, match="negative"):
        Worker(object(), verdict_wait_s=-1)


class Unused:
    """A session that fails the test if the refusal reaches the database."""

    def __getattr__(self, name):
        raise AssertionError(f"enqueue touched the session ({name}) before refusing")


@pytest.mark.parametrize("seed", ["search", "user", "citation", "doi", "sitemap", "diversity"])
async def test_only_a_followed_link_can_be_held(seed) -> None:
    assert seed not in HELD_SOURCES
    with pytest.raises(ValueError, match="verdict"):
        await enqueue(Unused(), "https://a.example.invalid/x", seed_source=seed, hold_until=NOW)


def _sql(clause) -> str:
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_a_hold_is_only_a_row_never_attempted() -> None:
    """A retry's backoff also sets `next_attempt_at`; mistaking it for a hold would let
    the last resort claim a failing URL in a loop."""
    sql = _sql(held(NOW))
    assert "queue.attempts = 0" in sql
    assert "queue.seed_source IN ('frontier')" in sql
    assert "queue.next_attempt_at >" in sql


def test_ready_takes_held_rows_only_when_asked() -> None:
    plain, wide = _sql(ready(NOW)), _sql(ready(NOW, include_held=True))
    assert "attempts" not in plain
    assert "queue.attempts = 0" in wide


# --------------------------------------------------------------------------
# The claim order
# --------------------------------------------------------------------------


class Task:
    def __init__(self) -> None:
        self.task_id = 1
        self.url_or_query = "https://a.example.invalid/x"
        self.attempts = 0
        self.topic = None
        self.task_type = "url"
        self.priority = 0


class Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def claiming(monkeypatch, *, answer_when):
    """A worker whose claims are recorded; a claim returns a task when ``answer_when`` says."""
    worker = Worker(
        object(),
        settings=WorkerSettings(worker_id="w", topics=("t",), directed_every=0),
        session_factory=Session,
    )
    calls: list[dict] = []

    async def claim_next(sess, **kwargs):
        calls.append(kwargs)
        return Task() if answer_when(kwargs) else None

    monkeypatch.setattr("worker.main.claim_next", claim_next)
    return worker, calls


async def test_a_waiting_link_is_asked_for_only_after_ready_work_is_not_found(monkeypatch) -> None:
    worker, calls = claiming(monkeypatch, answer_when=lambda kw: kw["include_held"])
    claim = await worker._claim()
    assert claim is not None, "nothing ready and a waiting link: the lane idled"
    assert [c["include_held"] for c in calls] == [False, True]


async def test_ready_work_never_reaches_for_a_waiting_link(monkeypatch) -> None:
    worker, calls = claiming(monkeypatch, answer_when=lambda kw: True)
    assert await worker._claim() is not None
    assert [c["include_held"] for c in calls] == [False]


async def test_an_empty_queue_still_answers_none(monkeypatch) -> None:
    worker, calls = claiming(monkeypatch, answer_when=lambda kw: False)
    assert await worker._claim() is None
    assert len(calls) == 2


def test_the_hold_and_the_directed_claim_name_the_same_followed_rows() -> None:
    """Drift: a held source must be one the directed claim already leaves out."""
    assert set(HELD_SOURCES) <= set(queueing.FOLLOWED_SOURCES)


async def test_the_unfiltered_last_resort_also_falls_back(monkeypatch) -> None:
    """With no topic filter the vector's draws come first; the waiting link still comes last."""
    worker = Worker(
        object(), settings=WorkerSettings(worker_id="w", directed_every=0), session_factory=Session
    )
    calls: list[dict] = []

    async def claim_next(sess, **kwargs):
        calls.append(kwargs)
        return Task() if kwargs["include_held"] else None

    async def no_shares(sess):
        return {}

    monkeypatch.setattr("worker.main.claim_next", claim_next)
    monkeypatch.setattr(worker, "_shares", no_shares)
    assert await asyncio.wait_for(worker._claim(), timeout=5) is not None
    assert calls[-1]["include_held"] is True
    assert all(not c["include_held"] for c in calls[:-1])
