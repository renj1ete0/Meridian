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
from meridian_core.crawlhealth import STALL_AFTER, judge
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
        judge(last_attempt_at=last, ready=r, pending=p, now=NOW)
        for last in (RECENT, OLD, None)
        for r, p in ((0, 0), (0, 1), (1, 1))
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
