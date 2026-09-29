"""The host verdict at its edges (task B-48)."""

from __future__ import annotations

import pytest

from meridian_core.hostscores import (
    DOWNRANKED_PRIORITY,
    EXPLORE_PENDING,
    FULL_SHARE,
    MAX_PENDING,
    MIN_EXAMINED,
    OFFTOPIC_SHARE,
    PROVEN_BOOST,
    HostPolicy,
    Score,
    Standing,
    decide,
    follows_links,
    site_of,
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


def test_a_thin_on_topic_hosts_links_are_scaled_by_its_share() -> None:
    thin = Score(100, 10)  # 10%: on-topic, well under the full share
    decision = decide(thin, government=False, pending=0)
    assert decision.queue and decision.reason == "on_topic_thin"
    assert decision.applied_to(60) == round(60 * 0.10 / FULL_SHARE)


def test_a_proven_host_ranks_above_every_unjudged_link(monkeypatch) -> None:
    """`B-115`: proven by yield, not by tier. The lowest tier's proven link must
    outrank the highest tier's unjudged one, or a proven news site still waits
    behind every government page nobody has judged."""
    full = Score(100, int(FULL_SHARE * 100))
    proven = decide(full, government=False, pending=0)
    unjudged = decide(Score(), government=False, pending=0)
    tiers = [5, 10, 30, 50, 60]
    assert proven.reason == "proven"
    assert min(proven.applied_to(t) for t in tiers) > max(unjudged.applied_to(t + 5) for t in tiers)


def test_the_boost_is_larger_than_any_tier_priority() -> None:
    """Drift: read the tier map rather than hardcoding it, so a tier raised
    past the boost fails here instead of quietly re-ordering the queue."""
    import yaml
    from pathlib import Path

    tiers = yaml.safe_load(
        (Path(__file__).parents[2] / "config" / "source_tiers.yaml").read_text()
    )["priority_by_tier"]
    assert PROVEN_BOOST > max(tiers.values())


def test_a_proven_host_is_still_capped() -> None:
    full = Score(100, int(FULL_SHARE * 100))
    assert decide(full, government=False, pending=MAX_PENDING).queue is False


def test_a_thin_on_topic_host_gets_no_boost() -> None:
    thin = Score(100, int(FULL_SHARE * 100) - 1)
    assert decide(thin, government=False, pending=0).applied_to(60) <= 60


def test_scaling_never_sinks_a_kept_link_to_the_downranked_or_held_band() -> None:
    barely = Score(100, int(OFFTOPIC_SHARE * 100))
    assert decide(barely, government=False, pending=0).applied_to(3) > DOWNRANKED_PRIORITY


def test_an_unknown_host_is_not_scaled() -> None:
    assert decide(Score(0, 0), government=False, pending=0).applied_to(60) == 60


# ---------------------------------------------------------------------------
# An unjudged subdomain takes an off-topic site's verdict (B-113)
# ---------------------------------------------------------------------------


def off_site(n: int = 5) -> dict[str, Score]:
    """Siblings under one site, each too thin to judge alone, off-topic together.

    A real public suffix, because the site is read from the suffix list and a
    reserved one such as `.test` is not on it.
    """
    return {f"s{i}.b113-site.com": Score(MIN_EXAMINED - 1, 0) for i in range(n)}


def test_siblings_too_thin_alone_judge_their_site_together() -> None:
    policy = HostPolicy(off_site())
    assert all(policy.score(h).standing is Standing.UNKNOWN for h in off_site())
    assert policy.site_score("new.b113-site.com").standing is Standing.OFF_TOPIC


def test_an_unjudged_subdomain_of_an_offtopic_site_is_ordered_last_not_dropped() -> None:
    """Never dropped: a subdomain can share a registrable domain and nothing else."""
    decision = HostPolicy(off_site()).admit("https://new.b113-site.com/a", government=False)
    assert decision.queue is True
    assert decision.priority == DOWNRANKED_PRIORITY
    assert decision.reason == "site_off_topic"


def test_the_site_verdict_is_capped_like_any_unknown_host() -> None:
    policy = HostPolicy(off_site())
    decisions = [
        policy.admit(f"https://new.b113-site.com/{i}", government=False)
        for i in range(EXPLORE_PENDING + 1)
    ]
    assert [d.queue for d in decisions] == [True] * EXPLORE_PENDING + [False]
    assert decisions[-1].reason == "site_off_topic_capped"


def test_a_subdomain_with_its_own_verdict_keeps_it() -> None:
    """The site speaks only for hosts nobody has judged."""
    # On a topic by its own share, while the site as a whole is not.
    scores = off_site() | {"lab.b113-site.com": Score(MIN_EXAMINED, 2)}
    policy = HostPolicy(scores)
    assert policy.site_score("lab.b113-site.com").standing is Standing.OFF_TOPIC
    decision = policy.admit("https://lab.b113-site.com/a", government=False)
    assert decision.reason.startswith("on_topic") and decision.priority is None


def test_an_on_topic_site_lends_nothing_to_a_new_subdomain() -> None:
    policy = HostPolicy({"a.b113-site.com": Score(MIN_EXAMINED, MIN_EXAMINED)})
    assert policy.admit("https://new.b113-site.com/a", government=False).reason == "unknown"


def test_a_public_suffix_is_never_one_site() -> None:
    """Two government sites under one national suffix are two sites: grouping
    at the suffix would let one off-topic agency order every other one last."""
    policy = HostPolicy({"agency-a.gov.uk": Score(MIN_EXAMINED, 0)})
    assert policy.admit("https://agency-b.gov.uk/a", government=True).reason == "unknown"
    assert site_of("x.blog.gov.uk") == "blog.gov.uk"
    assert site_of("blog.gov.uk") is None
    assert site_of("agency.go.kr") is None


def test_a_bare_site_host_is_judged_as_itself() -> None:
    """The registrable domain itself has no parent to defer to."""
    policy = HostPolicy(off_site())
    assert policy.admit("https://b113-site.com/a", government=False).reason == "unknown"
