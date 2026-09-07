"""Topic matching from a URL path (`P1-28`, §5.5, §5.6).

The thing under test is a guess, so what matters is the shape of its mistakes.
A matcher that over-fires mislabels the frontier and, because every crawled page
passes its topic to the links it discovers, mislabels a whole subtree. A matcher
that under-fires just leaves URLs unmatched and deprioritised, which is
recoverable. These tests hold it to erring in the second direction.
"""

from __future__ import annotations

import dataclasses

import pytest

from worker.topicmatch import (
    MIN_MATCHABLE_LENGTH,
    TopicVocabulary,
    normalise_path,
)


@dataclasses.dataclass
class FakeTerm:
    """Enough of a GazetteerTerm for `from_terms`."""

    canonical: str
    topic_labels: list[str] | None = None
    aliases: list[str] | None = None
    ambiguous: bool = False


def vocab(*terms: FakeTerm, topics: tuple[str, ...] = ()) -> TopicVocabulary:
    return TopicVocabulary.from_terms(terms, topics)


# --------------------------------------------------------------------------
# Path normalisation


def test_only_the_path_is_matched_not_the_query() -> None:
    """Query strings are pagination and tracking, and matching them invents
    topics out of `?ref=alpha-newsletter`."""
    assert normalise_path("https://x.test/car-parks?topic=alpha") == "car parks"


def test_percent_escapes_are_decoded() -> None:
    assert normalise_path("https://x.test/park%2Dconnector") == "park connector"


def test_structural_segments_are_dropped() -> None:
    """`/en/index.html` is every CMS, and carries no topic."""
    assert normalise_path("https://x.test/en/subject/index.html") == "subject"


def test_separators_all_normalise_the_same_way() -> None:
    forms = ["/park-connector", "/park_connector", "/park.connector", "/Park/Connector"]
    assert {normalise_path(f"https://x.test{f}") for f in forms} == {"park connector"}


def test_a_pathless_url_normalises_to_nothing() -> None:
    assert normalise_path("https://x.test") == ""
    assert normalise_path("https://x.test/") == ""


# --------------------------------------------------------------------------
# Matching, and specifically not over-matching


def test_a_gazetteer_term_in_the_path_assigns_its_topic() -> None:
    v = vocab(FakeTerm("Riverside Trail Network", ["alpha"]))
    assert v.best_topic("https://x.test/places/riverside-trail-network/east") == "alpha"


def test_matching_is_on_whole_tokens_not_substrings() -> None:
    """The classic failure: `bus` matching `business`, `pub` matching `public`.

    Substring matching looks like it works on the first ten URLs and then tags
    half the corpus. Both of these paths must match nothing.
    """
    v = vocab(FakeTerm("Buses", ["beta-service"]), FakeTerm("Pubs", ["alpha"]))
    assert v.best_topic("https://x.test/business/annual-report") is None
    assert v.best_topic("https://x.test/publications/2024") is None


def test_an_unmatched_url_gets_no_topic_rather_than_a_borrowed_one() -> None:
    v = vocab(FakeTerm("Riverside Trail Network", ["alpha"]))
    assert v.best_topic("https://x.test/facilities/rates") is None
    assert v.topics_for("https://x.test/facilities/rates") == ()


def test_ambiguous_terms_are_never_matched() -> None:
    """§5.5: a path has no context, so an ambiguous form cannot be resolved.

    A short acronym routinely expands to two unrelated things, one of them from
    a different field entirely. Guessing from a URL is exactly the wrong
    resolution §5.5 says corrupts the graph invisibly.
    """
    v = vocab(FakeTerm("Regional Pricing Scheme", ["alpha"], ambiguous=True))
    assert v.best_topic("https://x.test/regional-pricing-scheme") is None


def test_terms_with_no_topic_labels_contribute_nothing() -> None:
    v = vocab(FakeTerm("Some Agency", None), FakeTerm("Other Agency", []))
    assert not v
    assert v.best_topic("https://x.test/some-agency") is None


def test_short_surface_forms_are_not_matchable() -> None:
    """Three-letter acronyms collide with ordinary path segments."""
    v = vocab(FakeTerm("ABC", ["beta-service"]), FakeTerm("XYZ", ["gamma-systems"]))
    assert not v
    assert v.best_topic("https://x.test/abc/xyz") is None


def test_aliases_match_as_well_as_canonicals() -> None:
    v = vocab(FakeTerm("Flexible Route Service", ["beta-service"], aliases=["FlexRoute"]))
    assert v.best_topic("https://x.test/services/flexroute/pilot") == "beta-service"


# --------------------------------------------------------------------------
# The topic names themselves — what makes a new topic work with no gazetteer


def test_a_topic_name_matches_without_any_gazetteer_term() -> None:
    """A gazetteer starts thin and grows (`P5-02` harvests it).

    Without this a newly added topic would sit inert: nothing would ever be
    assigned to it, so nothing would be crawled for it, so nothing would ever
    be harvested to populate it. That is a loop with no way in.
    """
    v = TopicVocabulary.from_terms((), ("robotics", "biology", "economics"))
    assert v.best_topic("https://x.test/research/robotics/grasping") == "robotics"
    assert v.best_topic("https://x.test/depts/economics") == "economics"


def test_a_kebab_case_topic_matches_its_path_form() -> None:
    v = TopicVocabulary.from_terms((), ("beta-service",))
    assert v.best_topic("https://x.test/services/beta-service/faq") == "beta-service"
    assert v.best_topic("https://x.test/services/beta_service/faq") == "beta-service"


def test_a_short_topic_name_is_still_subject_to_the_length_floor() -> None:
    v = TopicVocabulary.from_terms((), ("av",))
    assert v.best_topic("https://x.test/av/deployment") is None


# --------------------------------------------------------------------------
# Choosing between several matches


def test_the_longest_match_leads() -> None:
    """"riverside trail network" is better evidence than "places"."""
    v = vocab(
        FakeTerm("Places", ["biology"]),
        FakeTerm("Riverside Trail Network", ["alpha"]),
    )
    assert v.best_topic("https://x.test/places/riverside-trail-network") == "alpha"


def test_every_matched_topic_is_reported_even_though_one_is_chosen() -> None:
    """`queue.topic` holds one, but discarding the rest loses real information.

    The full set is logged, and `entities.topic_labels` is where multi-topic
    association actually lives once the graph exists.
    """
    v = vocab(FakeTerm("Driverless Shuttle", ["gamma-systems", "beta-service"]))
    assert set(v.topics_for("https://x.test/driverless-shuttle/trial")) == {
        "gamma-systems",
        "beta-service",
    }


def test_one_phrase_reached_by_two_terms_keeps_both_topics() -> None:
    v = vocab(
        FakeTerm("Shared Pathways", ["alpha"]),
        FakeTerm("Shared Pathways", ["beta-service"]),
    )
    assert set(v.topics_for("https://x.test/shared-pathways")) == {
        "alpha",
        "beta-service",
    }


# --------------------------------------------------------------------------
# Degradation


def test_an_empty_vocabulary_matches_nothing_and_does_not_raise() -> None:
    """A worker that could not read its vocabulary crawls with none."""
    v = TopicVocabulary()
    assert not v
    assert v.best_topic("https://x.test/anything") is None
    assert v.topics_for("https://x.test/anything") == ()


@pytest.mark.parametrize(
    "url", ["", "not a url", "https://", "mailto:a@b.test", "https://x.test"]
)
def test_degenerate_urls_do_not_raise(url: str) -> None:
    v = TopicVocabulary.from_terms((), ("robotics",))
    assert v.best_topic(url) is None


def test_min_matchable_length_is_what_the_tests_assume() -> None:
    assert MIN_MATCHABLE_LENGTH == 5
