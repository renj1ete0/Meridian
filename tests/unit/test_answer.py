"""Grouping one search's hits into an answer (`meridian_core.answer`).

Pure: hits are built by hand, no database. The claims worth checking are the
ones a reader relies on without being able to see them — that a city is counted
once for its country, that a group of five items is five documents, that the
coverage verdict follows its stated rule at the boundary, and that a source
never examined for places is not presented as one about nowhere.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from meridian_core import answer
from meridian_core.answer import (
    COVERAGE_RULE,
    PRIMARY_TIERS,
    STRONG_MIN_PUBLISHERS,
    AnswerGroup,
    AnswerItem,
    countries_of,
    coverage_of,
    group_hits,
)
from meridian_core.models.source import SOURCE_TIER
from meridian_core.schemas.answer import AnswerGroupRead, AnswerItemRead
from meridian_core.search import SearchHit

_chunk = iter(range(1, 10_000))


def hit(
    source_id: int,
    *,
    score: float = 1.0,
    places: list[str] | None = None,
    tier: str = "press",
    host: str | None = None,
    date: dt.date | None = None,
    www: bool = True,
    text: str | None = None,
) -> SearchHit:
    host = host or f"pub{source_id}.example"
    return SearchHit(
        chunk_id=next(_chunk),
        source_id=source_id,
        text=text if text is not None else f"passage from {source_id} at {score}",
        page_or_offset=None,
        chunk_index=0,
        url=f"https://{'www.' if www else ''}{host}/doc/{source_id}",
        title=f"Doc {source_id}",
        source_tier=tier,
        publication_date=date,
        language="en",
        topic_labels=None,
        passage_topics=None,
        page_unit=None,
        media_type=None,
        duplicate_of=None,
        score=score,
        lexical_rank=1,
        vector_rank=None,
        places=places,
    )


# --------------------------------------------------------------------------
# Drift: the DTOs carry exactly the dataclasses' fields
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cls", "dto"), [(AnswerItem, AnswerItemRead), (AnswerGroup, AnswerGroupRead)]
)
def test_dto_fields_match_the_dataclass(cls, dto) -> None:
    assert {f.name for f in dataclasses.fields(cls)} == set(dto.model_fields)


def test_primary_tiers_are_real_tiers() -> None:
    """A tier renamed in the database and not here would make every group thin."""
    allowed = set(SOURCE_TIER.enums)
    assert allowed >= PRIMARY_TIERS
    assert set(answer.TIER_WEIGHT) == allowed


def test_the_rule_in_words_names_the_threshold() -> None:
    assert str(STRONG_MIN_PUBLISHERS) in COVERAGE_RULE
    assert "government" in COVERAGE_RULE and "peer-reviewed" in COVERAGE_RULE


# --------------------------------------------------------------------------
# Places
# --------------------------------------------------------------------------


def test_cities_roll_up_into_their_country_once() -> None:
    assert countries_of(["DE", "DEBER", "DEMUC", "FR"]) == ["DE", "FR"]
    assert countries_of(["JPTYO"]) == ["JP"]
    assert countries_of(None) == []
    assert countries_of([]) == []


def test_a_source_about_two_cities_is_one_source_for_the_country() -> None:
    groups, rest = group_hits([hit(1, places=["DEBER", "DEMUC"]), hit(2, places=["DE"])])
    assert rest is None
    assert [g.code for g in groups] == ["DE"]
    assert groups[0].sources == 2
    assert groups[0].name == "Germany"


def test_a_source_about_two_countries_counts_in_both() -> None:
    groups, _ = group_hits([hit(1, places=["DE", "FR"])])
    assert sorted(g.code for g in groups) == ["DE", "FR"]


def test_unplaced_separates_never_examined_from_about_nowhere() -> None:
    groups, rest = group_hits([hit(1, places=[]), hit(2, places=None), hit(3, places=None)])
    assert groups == []
    assert rest is not None
    assert rest.code is None
    assert rest.sources == 3
    assert rest.unexamined == 2


# --------------------------------------------------------------------------
# One item per source
# --------------------------------------------------------------------------


def test_one_item_per_source_carrying_its_best_passage() -> None:
    weak = hit(1, score=0.1, places=["FR"])
    strong = hit(1, score=0.9, places=["FR"])
    other = hit(2, score=0.5, places=["FR"])
    groups, _ = group_hits([weak, other, strong])
    items = groups[0].items
    assert [i.source_id for i in items] == [1, 2]
    assert items[0].chunk_id == strong.chunk_id
    assert items[0].passages == 2
    assert groups[0].sources == 2


BODY = (
    "Fares were integrated across operators in 2019, and ridership on the "
    "demand-responsive routes rose by a fifth in the first year."
)


def test_a_heading_does_not_stand_for_a_source_that_has_a_passage() -> None:
    """A heading scores well because it is little more than the question's words."""
    heading = hit(
        1, score=0.95, places=["FR"], text="Mobility-on-demand versus fixed-route transit"
    )
    body = hit(1, score=0.40, places=["FR"], text=BODY)
    groups, _ = group_hits([heading, body])
    item = groups[0].items[0]
    assert item.chunk_id == body.chunk_id
    assert item.passages == 2, "the heading still counts as a matching passage"


def test_a_source_with_only_a_heading_is_still_shown_by_it() -> None:
    heading = hit(1, score=0.95, places=["FR"], text="Mobility-on-demand")
    tiny = hit(1, score=0.10, places=["FR"], text="Contents")
    groups, _ = group_hits([tiny, heading])
    assert groups[0].items[0].chunk_id == heading.chunk_id


def test_among_passages_long_enough_the_better_score_wins() -> None:
    low = hit(1, score=0.3, places=["FR"], text=BODY)
    high = hit(1, score=0.6, places=["FR"], text=BODY + " Again.")
    groups, _ = group_hits([low, high])
    assert groups[0].items[0].chunk_id == high.chunk_id


def test_whitespace_does_not_make_a_heading_long_enough() -> None:
    padded = hit(1, score=0.9, places=["FR"], text="Heading" + " " * 200 + "\n" * 20)
    body = hit(1, score=0.2, places=["FR"], text=BODY)
    groups, _ = group_hits([padded, body])
    assert groups[0].items[0].chunk_id == body.chunk_id


def test_top_caps_items_but_not_the_counts() -> None:
    hits = [hit(i, places=["IT"], score=1 / i) for i in range(1, 9)]
    groups, _ = group_hits(hits, top=3)
    assert len(groups[0].items) == 3
    assert groups[0].sources == 8


def test_primary_tiers_and_new_publishers_lead() -> None:
    hits = [
        hit(1, score=1.0, places=["ES"], host="same.example"),
        hit(2, score=0.95, places=["ES"], host="same.example"),
        hit(3, score=0.8, places=["ES"], host="other.example"),
        hit(4, score=0.75, places=["ES"], tier="government", host="gov.example"),
    ]
    groups, _ = group_hits(hits)
    order = [i.source_id for i in groups[0].items]
    # 0.75 * 1.5 beats 1.0 * 1.0; the second item from `same` is halved.
    assert order == [4, 1, 3, 2]


# --------------------------------------------------------------------------
# Coverage: the rule, at its boundary
# --------------------------------------------------------------------------


def _country(n: int, *, primary: bool, same_host: bool = False) -> list[SearchHit]:
    return [
        hit(
            100 + i,
            places=["NL"],
            tier="government" if primary and i == 0 else "press",
            host="one.example" if same_host else None,
        )
        for i in range(n)
    ]


def test_strong_needs_enough_publishers_and_a_primary_source() -> None:
    groups, _ = group_hits(_country(STRONG_MIN_PUBLISHERS, primary=True))
    assert groups[0].coverage == "strong"


def test_one_publisher_short_is_thin() -> None:
    groups, _ = group_hits(_country(STRONG_MIN_PUBLISHERS - 1, primary=True))
    assert groups[0].coverage == "thin"


def test_many_sources_without_a_primary_one_is_thin() -> None:
    groups, _ = group_hits(_country(STRONG_MIN_PUBLISHERS + 5, primary=False))
    assert groups[0].coverage == "thin"


def test_coverage_of_rejects_a_primary_source_among_too_few_publishers() -> None:
    assert coverage_of({"a.example": ["government"], "b.example": ["peer_reviewed"]}) == "thin"
    assert coverage_of({"a": ["press"], "b": ["press"], "c": ["peer_reviewed"]}) == "strong"
    assert coverage_of({"a": ["press"], "b": ["institutional"], "c": ["informal"]}) == "thin"


def test_many_sources_from_one_publisher_is_thin() -> None:
    """Independence is by publisher: ten pages from one site are one voice."""
    groups, _ = group_hits(_country(10, primary=True, same_host=True))
    assert groups[0].sources == 10
    assert groups[0].publishers == 1
    assert groups[0].coverage == "thin"


def test_www_does_not_make_a_second_publisher() -> None:
    groups, _ = group_hits(
        [
            hit(1, places=["PT"], host="a.example"),
            hit(2, places=["PT"], host="a.example", www=False),
        ]
    )
    assert groups[0].publishers == 1


def test_groups_order_strong_first_then_by_publishers() -> None:
    hits = [
        *[hit(10 + i, places=["BE"]) for i in range(5)],  # thin, 5 publishers
        *[hit(20 + i, places=["AT"], tier="government") for i in range(3)],  # strong
        hit(30, places=["CH"]),  # thin, 1
    ]
    groups, _ = group_hits(hits)
    assert [g.code for g in groups] == ["AT", "BE", "CH"]
    assert [g.coverage for g in groups] == ["strong", "thin", "thin"]


def test_tier_mix_newest_and_empty_input() -> None:
    groups, _ = group_hits(
        [
            hit(1, places=["SE"], tier="government", date=dt.date(2021, 1, 1)),
            hit(2, places=["SE"], date=dt.date(2024, 5, 6)),
            hit(3, places=["SE"]),
        ]
    )
    assert groups[0].tier_mix == {"government": 1, "press": 2}
    assert groups[0].newest == dt.date(2024, 5, 6)
    assert group_hits([]) == ([], None)


def test_leading_topics_count_sources_not_passages() -> None:
    a1 = dataclasses.replace(hit(1), topic_labels=["x"])
    a2 = dataclasses.replace(hit(1), topic_labels=["x"])
    a3 = dataclasses.replace(hit(1), topic_labels=["x"])
    b = dataclasses.replace(hit(2), topic_labels=["y", "z"])
    c = dataclasses.replace(hit(3), topic_labels=["y"])
    assert answer.leading_topics([a1, a2, a3, b, c]) == ["y", "x", "z"]
    assert answer.leading_topics([a1, b, c], n=1) == ["y"]
    assert answer.leading_topics([hit(4)]) == []
