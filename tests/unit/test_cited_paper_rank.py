"""Where a cited paper sits among everything else in the queue (task B-58).

Read against the real tier map and the real constants of every other channel,
never against numbers copied here: the point of the rule is its position
relative to search results, queries and links, and those move when somebody
re-weights a tier. A test with the numbers written in would keep passing
after the ordering it was written to protect had gone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from meridian_core.ageing import HALF_LIFE_DAYS
from meridian_core.citedpapers import (
    CITED_TIER,
    FLOOR_PRIORITY,
    UNJUDGED_PRIORITY,
    cited_priority,
    on_topic_priority,
)
from meridian_core.hostscores import Standing
from meridian_core.tiering import priority_for_tier, urgency_for_tier
from worker.main import RESOLVED_PAPER_PRIORITY, SEARCH_RESULT_BONUS
from worker.requeue import HELD_PRIORITY
from worker.seedsearch import QUERY_PRIORITY

CONFIG = Path(__file__).resolve().parents[2] / "config" / "source_tiers.yaml"


@pytest.fixture(scope="module")
def tiers() -> dict:
    return yaml.safe_load(CONFIG.read_text())


def link(tier: str, tiers: dict) -> int:
    """What a followed link of this tier is queued at."""
    return priority_for_tier(tier, tiers) + urgency_for_tier(tier, HALF_LIFE_DAYS)


def search(tier: str, tiers: dict) -> int:
    return link(tier, tiers) + SEARCH_RESULT_BONUS


def other_tiers(tiers: dict) -> list[str]:
    return [t for t in tiers["priority_by_tier"] if t != CITED_TIER]


def test_the_cited_tier_is_in_the_map(tiers: dict) -> None:
    """A renamed tier would make `priority_for_tier` answer 0 in silence."""
    assert CITED_TIER in tiers["priority_by_tier"]


def test_an_on_topic_citation_competes_with_search_results(tiers: dict) -> None:
    """Above every other tier's search results, so a steady supply of them cannot
    starve it the way the old floor did; below a scholarly search result and
    the queries, which find new ground rather than deepen known ground."""
    cited = on_topic_priority(tiers)
    assert cited < search(CITED_TIER, tiers)
    assert cited < QUERY_PRIORITY
    for tier in other_tiers(tiers):
        assert cited > search(tier, tiers), tier


def test_an_unjudged_citation_is_modest(tiers: dict) -> None:
    """Above informal and press links, below institutional ones."""
    by_tier = sorted(tiers["priority_by_tier"], key=lambda t: link(t, tiers))
    lowest_two, rest = by_tier[:2], by_tier[2:]
    assert all(link(t, tiers) < UNJUDGED_PRIORITY for t in lowest_two)
    assert all(link(t, tiers) > UNJUDGED_PRIORITY for t in rest)


def test_the_floor_is_below_every_tier_and_above_held_links(tiers: dict) -> None:
    assert all(link(t, tiers) > FLOOR_PRIORITY for t in tiers["priority_by_tier"])
    assert FLOOR_PRIORITY > HELD_PRIORITY
    # The worker's own name for it must not drift from the rule's.
    assert RESOLVED_PAPER_PRIORITY == FLOOR_PRIORITY


@pytest.mark.parametrize("labels", [["a"], [], None])
def test_an_off_topic_host_is_the_floor_whatever_the_page_says(tiers: dict, labels) -> None:
    assert cited_priority(labels, Standing.OFF_TOPIC, tiers) == FLOOR_PRIORITY


@pytest.mark.parametrize("standing", [Standing.ON_TOPIC, Standing.UNKNOWN])
def test_the_page_decides_on_a_host_that_is_not_off_topic(tiers: dict, standing) -> None:
    assert cited_priority(["a"], standing, tiers) == on_topic_priority(tiers)
    assert cited_priority(None, standing, tiers) == UNJUDGED_PRIORITY
    assert cited_priority([], standing, tiers) == FLOOR_PRIORITY


def test_a_map_that_ranks_scholarship_low_cannot_invert_the_rule() -> None:
    """A confirmed citation never ranks under an unconfirmed one."""
    low = {"priority_by_tier": {CITED_TIER: 1}}
    assert cited_priority(["a"], Standing.ON_TOPIC, low) > cited_priority(
        None, Standing.ON_TOPIC, low
    )
