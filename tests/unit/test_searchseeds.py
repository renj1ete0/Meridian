"""Search seeds from what a topic is (task B-51, spec §7.4)."""

from __future__ import annotations

import pytest

from meridian_core.searchseeds import NEWS_BANG, TopicSeedInput, candidates, plan

WALK = TopicSeedInput(
    "walkability",
    "How easy and pleasant a place is to walk around in",
    ("footpath quality", "pedestrian crossing"),
)
BUS = TopicSeedInput("on-demand-bus", None, ("demand responsive transport",))


def texts(queries) -> list[str]:
    return [q.text for q in queries]


def test_every_shape_is_produced() -> None:
    kinds = {q.kind for q in candidates(WALK)}
    assert kinds == {"concept", "evidence", "counter", "news", "with_topic", "pair", "description"}


def test_news_queries_use_the_news_category() -> None:
    news = [q for q in candidates(WALK) if q.kind == "news"]
    assert news and all(q.text.startswith(f"{NEWS_BANG} ") for q in news)


def test_a_slug_is_searched_as_words() -> None:
    assert "on demand bus" in texts(candidates(BUS))
    assert not any("on-demand-bus" in t for t in texts(candidates(BUS)))


def test_a_topic_with_no_vocabulary_still_has_queries() -> None:
    bare = TopicSeedInput("robotics", None, ())
    assert "robotics" in texts(candidates(bare))
    assert "criticism of robotics" in texts(candidates(bare))


def test_short_terms_are_left_out() -> None:
    queries = texts(candidates(TopicSeedInput("walkability", None, ("AV", "abc"))))
    assert not any(q in ("AV", "abc") for q in queries)


def test_a_description_too_short_to_be_a_phrase_is_not_a_query() -> None:
    kinds = {q.kind for q in candidates(TopicSeedInput("walkability", "walking", ()))}
    assert "description" not in kinds


def test_plan_never_repeats_a_query_already_queued_whatever_its_case() -> None:
    first = plan([WALK], already=[], per_topic=5, seed=1)
    again = plan([WALK], already=[q.text.upper() for q in first], per_topic=5, seed=1)
    assert not set(texts(first)) & set(texts(again))


def test_plan_takes_one_of_each_leading_shape_before_filling() -> None:
    picked = plan([WALK], already=[], per_topic=4, seed=3)
    assert {q.kind for q in picked} == {"news", "counter", "concept", "evidence"}


def test_plan_is_per_topic_and_capped() -> None:
    picked = plan([WALK, BUS], already=[], per_topic=3, seed=0)
    assert [q.topic for q in picked].count("walkability") == 3
    assert [q.topic for q in picked].count("on-demand-bus") == 3


def test_plan_is_reproducible_by_seed_and_varies_across_seeds() -> None:
    assert texts(plan([WALK], already=[], per_topic=8, seed=7)) == texts(
        plan([WALK], already=[], per_topic=8, seed=7)
    )
    runs = {tuple(texts(plan([WALK], already=[], per_topic=8, seed=s))) for s in range(5)}
    assert len(runs) > 1


def test_a_topic_whose_queries_are_exhausted_yields_nothing() -> None:
    everything = texts(candidates(BUS))
    assert plan([BUS], already=everything, per_topic=5, seed=0) == []


def test_a_nonsense_budget_is_refused() -> None:
    with pytest.raises(ValueError):
        plan([WALK], already=[], per_topic=0, seed=0)
