"""An unjudged host that on-topic pages elsewhere link to is explored first (task B-150).

Measured on the live corpus: hosts linked from an on-topic page on another site went on to
be on a topic several times as often as hosts nobody on-topic linked to. These tests hold the
ordering that measurement justifies: above unjudged, below proven, and never over a host's
own record.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from meridian_core.hostscores import (
    DOWNRANKED_PRIORITY,
    EXPLORE_PENDING,
    FULL_SHARE,
    MIN_EXAMINED,
    MIN_FOLLOWED,
    VOUCHED_BOOST,
    VOUCHED_MIN,
    VOUCHED_PENDING,
    HostPolicy,
    Score,
    Standing,
    decide,
)
from worker.requeue_links import PROMOTED_PRIORITY, VOUCHED_PRIORITY

TIERS = yaml.safe_load((Path(__file__).parents[2] / "config" / "source_tiers.yaml").read_text())[
    "priority_by_tier"
]

VOUCHED = Score(vouched=VOUCHED_MIN)
PROVEN = Score(100, 25, followed_examined=100, followed_on_topic=int(FULL_SHARE * 100))


def test_a_vouched_host_ranks_between_unjudged_and_proven() -> None:
    vouched = decide(VOUCHED, government=False, pending=0)
    unjudged = decide(Score(), government=False, pending=0)
    proven = decide(PROVEN, government=False, pending=0)
    assert vouched.reason == "vouched"
    for tier in TIERS.values():
        assert unjudged.applied_to(tier) < vouched.applied_to(tier) < proven.applied_to(tier)


def test_a_vouched_host_is_explored_further_but_still_capped() -> None:
    assert VOUCHED_PENDING > EXPLORE_PENDING
    assert decide(VOUCHED, government=False, pending=VOUCHED_PENDING - 1).queue
    capped = decide(VOUCHED, government=False, pending=VOUCHED_PENDING)
    assert (capped.queue, capped.reason) == (False, "vouched_capped")


def test_a_host_judged_by_its_own_pages_ignores_vouches() -> None:
    """Once its pages are read, the host's record decides, however many sites vouch."""
    off = Score(MIN_EXAMINED, 0, vouched=50)
    assert off.standing is Standing.OFF_TOPIC and not off.is_vouched
    assert not decide(off, government=False, pending=0).queue


def test_too_few_vouches_is_just_unjudged() -> None:
    assert not Score(vouched=VOUCHED_MIN - 1).is_vouched


def test_a_vouch_outweighs_an_off_topic_sites_verdict_on_its_subdomain() -> None:
    """A site's verdict speaks for subdomains nobody has judged; a vouch is about this one."""
    policy = HostPolicy(
        {
            "www.bigsite.org": Score(MIN_EXAMINED * 5, 0),
            "lab.bigsite.org": Score(vouched=VOUCHED_MIN),
            "office.bigsite.org": Score(),
        }
    )
    assert policy.admit("https://lab.bigsite.org/a", government=False).reason == "vouched"
    assert policy.admit("https://office.bigsite.org/a", government=False).priority == (
        DOWNRANKED_PRIORITY
    )


def test_the_raise_levels_match_what_admission_gives() -> None:
    """A raised link sits where a newly queued one of the lowest tier would."""
    lowest = min(TIERS.values())
    assert decide(VOUCHED, government=False, pending=0).applied_to(lowest) == VOUCHED_PRIORITY
    assert decide(PROVEN, government=False, pending=0).applied_to(lowest) == PROMOTED_PRIORITY


# ---------------------------------------------------------------------------
# Proven by following (B-155)
#
# Backtested on three loop runs: of five hosts proven only on pages a search picked, four
# yielded 1-5% on their followed links; where a host had a followed record, its followed share
# predicted the next followed page better than its share over every page.


def test_a_host_on_topic_only_by_search_picked_pages_is_promising_not_proven() -> None:
    searched = Score(21, 14)  # two thirds on a topic, every one of them found by a search
    decision = decide(searched, government=False, pending=0)
    assert (decision.reason, decision.boost) == ("promising", VOUCHED_BOOST)
    assert decide(searched, government=False, pending=VOUCHED_PENDING).reason == (
        "promising_capped"
    )


def test_a_followed_record_proves_a_host() -> None:
    followed = Score(40, 30, followed_examined=MIN_FOLLOWED, followed_on_topic=MIN_FOLLOWED)
    assert decide(followed, government=False, pending=VOUCHED_PENDING).reason == "proven"


def test_a_poor_followed_record_outweighs_good_search_results() -> None:
    """On-topic by everything examined, but what following found is mostly elsewhere."""
    mixed = Score(330, 82, followed_examined=229, followed_on_topic=16)  # 25% overall, 7% followed
    decision = decide(mixed, government=False, pending=0)
    assert decision.reason == "on_topic_thin"
    assert decision.weight == pytest.approx(16 / 229 / FULL_SHARE)


def test_an_off_topic_host_stays_off_topic_whatever_following_found() -> None:
    """The off-topic gate still reads every examined page."""
    off = Score(100, 2, followed_examined=MIN_FOLLOWED, followed_on_topic=MIN_FOLLOWED)
    assert not decide(off, government=False, pending=0).queue


def test_every_score_field_is_stored() -> None:
    """A field the table does not hold is lost between the hourly pass and the fetch loop, as
    `requeue` once lost two by rebuilding scores from the counts it knew."""
    import dataclasses

    from meridian_core.models import HostScore

    assert {f.name for f in dataclasses.fields(Score)} <= set(HostScore.__table__.columns.keys())
