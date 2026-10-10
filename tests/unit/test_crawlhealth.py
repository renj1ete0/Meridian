"""The crawl-health verdict and its DTOs, with no database (task P6-25, §13.4).

The verdict is a pure function precisely so every branch can be driven here:
"the queue is empty" is not a state a shared dev database can be put into, and
the branch that most needs a test — stalled versus merely backing off — is the
one a live crawl will almost never show on demand.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import typing

import pytest

from meridian_core import crawlhealth
from meridian_core.crawlhealth import RESUME_SHARE, STALL_AFTER, judge
from meridian_core.queueing import DEFAULT_LEASE_SECONDS
from meridian_core.schemas import search as dtos
from meridian_core.schemas.enums import LivenessState

NOW = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.UTC)
RECENT = NOW - dt.timedelta(minutes=1)
OLD = NOW - STALL_AFTER - dt.timedelta(seconds=1)


@pytest.mark.parametrize(
    "last,ready,pending,expected",
    [
        # A recent fetch is crawling whatever the queue says: the last rows of
        # a draining queue are still being worked.
        (RECENT, 5, 5, "crawling"),
        (RECENT, 0, 0, "crawling"),
        # Silence with claimable work: the failure the screen exists for.
        (OLD, 3, 3, "stalled"),
        # Never fetched, with work: a worker that never started.
        (None, 1, 1, "stalled"),
        # Silence with work that is all backing off: the queue doing its job.
        (OLD, 0, 4, "waiting"),
        (None, 0, 4, "waiting"),
        # Silence with nothing to do.
        (OLD, 0, 0, "idle"),
        (None, 0, 0, "idle"),
    ],
)
def test_the_verdict(last, ready, pending, expected) -> None:
    assert judge(last_attempt_at=last, ready=ready, pending=pending, now=NOW) == expected


def test_the_threshold_is_inclusive_on_the_quiet_side() -> None:
    """Exactly one lease of silence is still crawling; one second more is not."""
    edge = NOW - STALL_AFTER
    assert judge(last_attempt_at=edge, ready=1, pending=1, now=NOW) == "crawling"
    assert (
        judge(last_attempt_at=edge - dt.timedelta(seconds=1), ready=1, pending=1, now=NOW)
        == "stalled"
    )


def test_the_threshold_is_the_claim_lease() -> None:
    """Tied to the lease rather than restated: if the lease is ever lengthened
    because fetches got slower, a stall threshold left behind would call every
    slow fetch an outage."""
    assert dt.timedelta(seconds=DEFAULT_LEASE_SECONDS) == STALL_AFTER


def test_every_state_is_reachable() -> None:
    """A state in the Literal that `judge` can never return is a state the
    screen has words for and will never show — and the reverse is a 500."""
    reached = {
        judge(last_attempt_at=last, ready=r, pending=p, now=NOW, embed_backlog=b, ceiling=100)
        for last in (RECENT, OLD, None)
        for r, p in ((0, 0), (0, 1), (1, 1))
        for b in (None, 0, 1000)
    }
    assert reached == set(typing.get_args(LivenessState))


# --------------------------------------------------------------------------
# Dataclass ↔ DTO parity
# --------------------------------------------------------------------------

PAIRS = [
    (crawlhealth.CrawlHealth, dtos.CrawlHealthRead),
    (crawlhealth.HourBucket, dtos.HourBucketRead),
    (crawlhealth.OutcomeCount, dtos.OutcomeCountRead),
    (crawlhealth.DomainCount, dtos.DomainCountRead),
    (crawlhealth.Liveness, dtos.LivenessRead),
]


@pytest.mark.parametrize("dataclass,dto", PAIRS, ids=lambda x: x.__name__)
def test_the_dto_has_exactly_the_dataclass_fields(dataclass, dto) -> None:
    """Read off both sides, never listed: a field added to one and not the
    other is either dropped on the way out or never populated."""
    assert {f.name for f in dataclasses.fields(dataclass)} == set(dto.model_fields)


def test_the_whole_answer_validates_from_the_dataclass() -> None:
    """`from_attributes` through three levels of nesting, which is what the
    route relies on — and a bad outcome name is refused rather than passed on."""
    health = crawlhealth.CrawlHealth(
        as_of=NOW,
        stall_after_seconds=900,
        hours=[crawlhealth.HourBucket(start=NOW, succeeded=1, failed=2)],
        outcomes=[crawlhealth.OutcomeCount(outcome="timeout", count=2)],
        queue={"pending": 3},
        embedding_backlog=4,
        top_domains=[crawlhealth.DomainCount(domain="a.test", attempts=3, succeeded=1)],
        liveness=crawlhealth.Liveness(
            state="stalled", last_attempt_at=None, quiet_seconds=None, ready=3, pending=3
        ),
    )

    dto = dtos.CrawlHealthRead.model_validate(health)
    assert dto.hours[0].failed == 2
    assert dto.liveness.state == "stalled"

    bad = dataclasses.replace(health, outcomes=[crawlhealth.OutcomeCount(outcome="nope", count=1)])
    with pytest.raises(ValueError):
        dtos.CrawlHealthRead.model_validate(bad)

    unknown = dataclasses.replace(health, queue={"sleeping": 1})
    with pytest.raises(ValueError):
        dtos.CrawlHealthRead.model_validate(unknown)


# --------------------------------------------------------------------------
# A deliberate pause is not a stall (`B-203`)
# --------------------------------------------------------------------------


def test_ready_work_and_no_fetch_over_the_ceiling_is_a_pause_not_a_stall() -> None:
    """The worker stops claiming above the embedding ceiling (`B-61`); the pill said "crawl
    stalled" and Crawl health told the operator to check the worker was running."""
    quiet = NOW - dt.timedelta(hours=1)
    ceiling = 20_000
    paused = judge(
        last_attempt_at=quiet, ready=5, pending=5, now=NOW, embed_backlog=27_258, ceiling=ceiling
    )
    assert paused == "paused"
    # Resumes below 80% of the ceiling: between there and the ceiling it may still be paused.
    edge = int(ceiling * RESUME_SHARE)
    assert (
        judge(
            last_attempt_at=quiet,
            ready=5,
            pending=5,
            now=NOW,
            embed_backlog=edge + 1,
            ceiling=ceiling,
        )
        == "paused"
    )
    assert (
        judge(
            last_attempt_at=quiet, ready=5, pending=5, now=NOW, embed_backlog=edge, ceiling=ceiling
        )
        == "stalled"
    )
    # Backpressure off, or no count: a stall is a stall.
    assert (
        judge(last_attempt_at=quiet, ready=5, pending=5, now=NOW, embed_backlog=27_258, ceiling=0)
        == "stalled"
    )
    assert judge(last_attempt_at=quiet, ready=5, pending=5, now=NOW) == "stalled"
    # A crawl that is fetching is crawling, whatever the backlog.
    assert (
        judge(
            last_attempt_at=NOW, ready=5, pending=5, now=NOW, embed_backlog=99_999, ceiling=ceiling
        )
        == "crawling"
    )


def test_the_ceiling_is_read_as_the_worker_reads_it(monkeypatch) -> None:
    """One variable for both, so the screen's verdict matches the worker's behaviour."""
    from meridian_core.crawlhealth import (
        DEFAULT_MAX_EMBED_BACKLOG,
        EMBED_BACKLOG_ENV,
        embed_ceiling,
    )

    monkeypatch.delenv(EMBED_BACKLOG_ENV, raising=False)
    assert embed_ceiling() == DEFAULT_MAX_EMBED_BACKLOG
    monkeypatch.setenv(EMBED_BACKLOG_ENV, "0")
    assert embed_ceiling() == 0
    monkeypatch.setenv(EMBED_BACKLOG_ENV, "not a number")
    assert embed_ceiling() == DEFAULT_MAX_EMBED_BACKLOG
    monkeypatch.setenv(EMBED_BACKLOG_ENV, "-5")
    assert embed_ceiling() == 0


def test_every_compose_file_that_runs_the_worker_gives_the_api_the_same_ceiling() -> None:
    """Drift: a ceiling set for the worker and not the API would call its pause a stall."""
    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parents[2]
    for path in root.glob("docker-compose*.yml"):
        services = (yaml.safe_load(path.read_text()) or {}).get("services", {})
        worker, api = services.get("worker"), services.get("api")
        if not worker or not api or "env_file" in api:
            continue
        worker_env = worker.get("environment") or {}
        if "MERIDIAN_WORKER_MAX_EMBED_BACKLOG" in worker_env:
            assert worker_env["MERIDIAN_WORKER_MAX_EMBED_BACKLOG"] == (
                api.get("environment") or {}
            ).get("MERIDIAN_WORKER_MAX_EMBED_BACKLOG"), path.name
