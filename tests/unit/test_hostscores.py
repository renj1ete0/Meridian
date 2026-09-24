"""The host verdict at its edges (task B-48)."""

from __future__ import annotations

import pytest

from meridian_core.hostscores import (
    DOWNRANKED_PRIORITY,
    EXPLORE_PENDING,
    MAX_PENDING,
    MIN_EXAMINED,
    OFFTOPIC_SHARE,
    HostPolicy,
    Score,
    Standing,
    decide,
    follows_links,
)


def test_a_host_is_unknown_until_enough_pages_are_examined() -> None:
    assert Score(MIN_EXAMINED - 1, 0).standing is Standing.UNKNOWN
    assert Score(MIN_EXAMINED, 0).standing is Standing.OFF_TOPIC


def test_the_offtopic_line_is_a_share_not_a_count() -> None:
    at = int(OFFTOPIC_SHARE * 100)
    assert Score(100, at).standing is Standing.ON_TOPIC
    assert Score(100, at - 1).standing is Standing.OFF_TOPIC


def test_an_offtopic_host_is_refused_and_does_not_have_its_links_followed() -> None:
    off = Score(MIN_EXAMINED, 0)
    assert decide(off, government=False, pending=0).queue is False
    assert follows_links(off) is False


def test_an_offtopic_government_host_is_downranked_then_capped() -> None:
    off = Score(MIN_EXAMINED, 0)
    admitted = decide(off, government=True, pending=0)
    assert admitted.queue and admitted.priority == DOWNRANKED_PRIORITY
    assert decide(off, government=True, pending=EXPLORE_PENDING).queue is False


@pytest.mark.parametrize(
    ("score", "cap"),
    [(Score(0, 0), EXPLORE_PENDING), (Score(MIN_EXAMINED, MIN_EXAMINED), MAX_PENDING)],
)
def test_every_host_has_a_cap(score: Score, cap: int) -> None:
    assert decide(score, government=False, pending=cap - 1).queue is True
    assert decide(score, government=False, pending=cap).queue is False


def test_an_on_topic_host_keeps_the_callers_priority() -> None:
    assert decide(Score(MIN_EXAMINED, MIN_EXAMINED), government=False, pending=0).priority is None


def test_the_policy_counts_what_it_admits_between_recomputations() -> None:
    policy = HostPolicy({"a.test": Score(0, 0, pending=EXPLORE_PENDING - 1)})
    assert policy.admit("https://a.test/1", government=False).queue is True
    assert policy.admit("https://a.test/2", government=False).queue is False


def test_www_and_bare_are_one_host_to_the_policy() -> None:
    policy = HostPolicy({"a.test": Score(MIN_EXAMINED, 0)})
    assert policy.admit("https://www.a.test/x", government=False).queue is False


def test_a_link_with_no_host_is_refused() -> None:
    assert HostPolicy().admit("mailto:someone", government=False).queue is False


def test_a_fresh_read_resets_the_local_counts() -> None:
    policy = HostPolicy({"a.test": Score(0, 0, pending=EXPLORE_PENDING - 1)})
    policy.admit("https://a.test/1", government=False)
    policy.replace({"a.test": Score(0, 0, pending=0)})
    assert policy.admit("https://a.test/2", government=False).queue is True
