"""Gaps without a database: thresholds, ranking, pluggable sources (task P6-36)."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest
from pydantic import ValidationError

from meridian_core import gaps
from meridian_core.schemas.gaps import GapActionRead, GapBoost, GapRead, GapSeed, GapSourceRead

TODAY = dt.date(2026, 9, 24)


def cover(**over):
    base = dict(
        description=None,
        sources=0,
        strong=0,
        passages=0,
        newest=None,
        share=0.2,
        corpus_share=0.0,
        today=TODAY,
    )
    base.update(over)
    return {g.kind: g for g in gaps.coverage_gaps("t", **base)}


# -- drift: DTOs mirror the dataclasses --------------------------------------


@pytest.mark.parametrize(
    ("dto", "dc"),
    [(GapRead, gaps.Gap), (GapActionRead, gaps.Action), (GapSourceRead, gaps.SourceStatus)],
)
def test_every_dataclass_field_is_on_the_wire(dto, dc):
    assert {f.name for f in dataclasses.fields(dc)} == set(dto.model_fields)


def test_every_action_kind_a_source_can_emit_is_a_dto_kind():
    from typing import get_args

    from meridian_core.schemas.gaps import ActionKind

    q = gaps.QueryYield(1, "some words", 0, 0)
    per_query = gaps.query_gaps("t", description=None, answered=1, failing=[q])
    off_topic = gaps.off_topic_gap("t", description=None, examined=10, on_topic=0)
    emitted = (
        {a.kind for a in gaps._seed_and_boost("t", None)}
        | {"open_search"}
        | {a.kind for g in per_query for a in g.actions}
        | {a.kind for a in off_topic.actions}
    )
    assert emitted <= set(get_args(ActionKind))


# -- thresholds -----------------------------------------------------------------


def test_no_sources_is_thin_at_full_severity():
    assert cover(sources=0)["thin"].severity == 1.0


def test_thin_severity_falls_as_sources_rise_and_stops_at_the_threshold():
    s = [cover(sources=n)["thin"].severity for n in range(gaps.THIN_SOURCES)]
    assert s == sorted(s, reverse=True)
    assert "thin" not in cover(sources=gaps.THIN_SOURCES, strong=gaps.WEAK_STRONG)


def test_weak_needs_enough_sources_and_too_few_strong_ones():
    found = cover(sources=gaps.THIN_SOURCES, strong=gaps.WEAK_STRONG - 1)
    assert set(found) == {"weak"}
    assert not cover(sources=gaps.THIN_SOURCES, strong=gaps.WEAK_STRONG)


def test_stale_only_with_a_dated_source_older_than_the_limit():
    old = TODAY - dt.timedelta(days=365 * gaps.STALE_YEARS + 1)
    assert "stale" in cover(sources=20, strong=5, newest=old)
    assert not cover(sources=20, strong=5, newest=TODAY)
    assert not cover(sources=20, strong=5, newest=None)


def test_the_reason_states_the_absence_in_numbers():
    reason = cover(sources=1, passages=4, share=0.15, corpus_share=0.004)["thin"].reason
    assert "1 source " in reason and "4 passages" in reason and "15%" in reason


def test_a_seed_action_is_prefilled_from_the_description_else_the_name():
    seed, boost = gaps._seed_and_boost("on-demand-bus", None)
    assert seed.query == "on demand bus"
    seed, _ = gaps._seed_and_boost("x", "a b c d e f g h i j k")
    assert seed.query == "a b c d e f g h"
    assert (boost.factor, boost.days) == (gaps.BOOST_FACTOR, gaps.BOOST_DAYS)


# -- the question set -----------------------------------------------------------


def run(*items, draft=True):
    return {"set_version": 1, "draft": draft, "items": list(items)}


def item(id_, kind="lookup", proposed=None, operator=None):
    return {
        "id": id_,
        "kind": kind,
        "question": f"question {id_}",
        "topics": ["t"],
        "hits": [],
        "proposed": {"grade": proposed},
        "operator": operator,
    }


def test_operator_grades_outrank_proposals_and_only_low_items_are_gaps():
    found = {
        g.subject: g
        for g in gaps.question_gaps(
            run(
                item("A", proposed=0),
                item("B", operator={"grade": 1, "missing": "thin"}),
                item("C", operator={"grade": 2}),
                item("D", proposed=2),
            ),
            "r.yaml",
        )
    }
    assert set(found) == {"A", "B"}
    assert found["B"].severity > found["A"].severity
    assert found["A"].evidence["basis"] == "proposal"
    assert "not a score" in found["A"].title
    assert found["B"].reason.startswith("thin")


def test_a_low_item_from_a_lexical_only_run_says_so():
    lexical = {**run(item("A", proposed=0)), "context": {"mode": "lexical-only"}}
    hybrid = {**run(item("A", proposed=0)), "context": {"mode": "hybrid"}}
    assert gaps.LEXICAL_NOTE in gaps.question_gaps(lexical, "r")[0].reason
    assert gaps.LEXICAL_NOTE not in gaps.question_gaps(hybrid, "r")[0].reason


def test_a_heuristic_proposal_on_a_gap_item_is_not_a_gap():
    assert gaps.question_gaps(run(item("G", kind="gap", proposed=0)), "r") == []
    graded = gaps.question_gaps(run(item("G", kind="gap", operator={"grade": 0})), "r")
    assert [g.subject for g in graded] == ["G"]


def test_question_gaps_never_offer_to_steer():
    """eval/README.md: a question is never a seed or a steering reason."""
    for g in gaps.question_gaps(run(item("A", operator={"grade": 0})), "r"):
        assert {a.kind for a in g.actions} == {"open_search"}


def test_held_out_ids_are_refused_by_the_action_guard():
    with pytest.raises(gaps.HeldOut):
        gaps._check_not_held_out("question:Q01")
    gaps._check_not_held_out("topic-thin:x")


# -- ranking and pluggable sources ----------------------------------------------


def gap(id_, severity):
    return gaps.Gap(id_, "s", "k", "x", "t", "r", severity, {}, ())


def test_rank_is_by_severity_then_id_for_a_stable_order():
    ranked = gaps.rank([gap("b", 0.5), gap("a", 0.5), gap("c", 0.9)])
    assert [g.id for g in ranked] == ["c", "a", "b"]


async def test_find_gaps_reports_an_unavailable_source_and_keeps_the_rest():
    async def ok(sess):
        return [gap("x", 0.1)]

    async def broken(sess):
        raise gaps.SourceUnavailable("no run file")

    found, statuses = await gaps.find_gaps(None, sources={"ok": ok, "broken": broken})
    assert [g.id for g in found] == ["x"]
    assert {s.name: s.status for s in statuses} == {"ok": "ok", "broken": "unavailable"}


def test_the_built_sources_and_the_pending_ones_do_not_overlap():
    assert not set(gaps.SOURCES) & set(gaps.PENDING)
    built = {
        "topic-coverage",
        "place-coverage",
        "search-queries",
        "search-results",
        "question-set",
    }
    assert built <= set(gaps.SOURCES)


def test_the_per_topic_search_yield_source_is_replaced_not_duplicated():
    """P6-37 subsumes it: an all-failed topic would otherwise be listed twice."""
    assert "search-yield" not in gaps.SOURCES


def test_registering_a_pending_source_takes_it_off_the_pending_list(monkeypatch):
    monkeypatch.setattr(gaps, "SOURCES", dict(gaps.SOURCES))
    monkeypatch.setattr(gaps, "PENDING", dict(gaps.PENDING))

    @gaps.register("areas")
    async def areas(sess):
        return []

    assert "areas" in gaps.SOURCES and "areas" not in gaps.PENDING


# -- action DTO rejection -----------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"topic": "t", "query": "https://x.test", "gap_id": "g"},
        {"topic": "t", "query": "   ", "gap_id": "g"},
        {"topic": "t", "query": "ok words", "gap_id": ""},
        {"topic": "t", "query": "ok words", "gap_id": "g", "priority": 999},
    ],
)
def test_seed_payload_rejections(payload):
    with pytest.raises(ValidationError):
        GapSeed(**payload)


@pytest.mark.parametrize(("factor", "days"), [(1.0, 7), (0.5, 7), (6, 7), (2, 0), (2, 61)])
def test_boost_payload_rejections(factor, days):
    with pytest.raises(ValidationError):
        GapBoost(topic="t", factor=factor, days=days, gap_id="g")


# -- passages (P2-24) ----------------------------------------------------------


def test_passages_in_other_documents_are_counted_and_shown():
    thin = cover(sources=2, passages=9, passage_sources=3)["thin"]
    assert thin.evidence["passage_sources"] == 3
    assert "3 other documents" in thin.reason and "9 passages" in thin.reason


def test_passages_elsewhere_do_not_lift_a_topic_out_of_thin():
    """A topic that lives only as asides has no document to cite as about it."""
    found = cover(sources=0, passages=40, passage_sources=gaps.THIN_SOURCES * 3)
    assert "thin" in found
    assert found["thin"].title.startswith("No sources; passages in")
    assert found["thin"].severity == cover(sources=0)["thin"].severity


def test_no_passages_elsewhere_says_nothing_about_them():
    thin = cover(sources=1, passages=1)["thin"]
    assert thin.evidence["passage_sources"] == 0
    assert "other document" not in thin.reason
    assert cover(sources=0)["thin"].title == "No sources"


def test_a_weak_topic_mentions_passages_elsewhere():
    weak = cover(sources=gaps.THIN_SOURCES, strong=0, passages=50, passage_sources=2)["weak"]
    assert "2 other documents" in weak.reason


# -- place coverage (P2-23) ---------------------------------------------------


def place(sources: int, strong: int = 0) -> list[gaps.Gap]:
    return gaps.place_gaps("t", "JP", "a place", sources=sources, strong=strong, topic_sources=40)


def test_a_place_with_enough_sources_for_the_topic_is_not_a_gap():
    assert place(gaps.PLACE_THIN) == []
    assert len(place(gaps.PLACE_THIN - 1)) == 1


def test_an_empty_place_ranks_above_a_thin_one_and_below_an_empty_topic():
    """A topic with nothing at all is the first thing to fix; a table of empty
    cells under it must not bury that."""
    empty, thin = place(0)[0], place(gaps.PLACE_THIN - 1)[0]
    no_topic = cover(sources=0)["thin"]
    assert no_topic.severity > empty.severity > thin.severity > 0


def test_a_place_gap_names_its_place_and_offers_reading_and_seeding():
    gap = place(1, strong=1)[0]
    assert gap.id == "place-thin:t:JP"
    assert gap.source == "place-coverage"
    assert gap.evidence == {"place": "JP", "sources": 1, "strong_sources": 1, "topic_sources": 40}
    kinds = {a.kind: a for a in gap.actions}
    assert set(kinds) == {"seed_query", "open_search"}
    assert kinds["seed_query"].topic == "t"
    assert "a place" in kinds["seed_query"].query
    # The seed passes the same validation a person's seed does.
    GapSeed(topic="t", query=kinds["seed_query"].query, gap_id=gap.id)


# -- search queries (P6-37) --------------------------------------------------------


def qy(task_id, results, queued, query=None):
    return gaps.QueryYield(task_id, query or f"words {task_id}", results, queued)


@pytest.mark.parametrize(
    ("results", "queued", "kind"),
    [
        (0, 0, "search_empty"),
        (7, 0, "search_known"),
        (7, 3, None),  # productive: not a gap
        (None, None, None),  # answered before B-56: unmeasured, not a failure
        (None, 0, None),
        (0, None, None),
    ],
)
def test_query_failure_classifies_and_refuses_the_unmeasured(results, queued, kind):
    assert gaps.query_failure(results, queued) == kind


def test_productive_and_unmeasured_queries_make_no_gap():
    assert gaps.query_gaps("t", description=None, answered=2, failing=[qy(1, 5, 2)]) == []
    assert gaps.query_gaps("t", description=None, answered=0, failing=[qy(1, None, None)]) == []


def test_repeated_failures_are_one_gap_per_kind_not_one_per_query():
    failing = [qy(n, 0, 0) for n in range(9, 3, -1)] + [qy(2, 4, 0), qy(1, 6, 0)]
    found = {
        g.kind: g for g in gaps.query_gaps("t", description=None, answered=10, failing=failing)
    }
    assert set(found) == {"search_empty", "search_known"}
    empty = found["search_empty"]
    assert empty.evidence["failed"] == 6 and empty.evidence["searches_done"] == 10
    assert empty.id == "search-empty:t" and found["search_known"].id == "search-known:t"
    # Quoted up to the limit, the rest counted; the newest failure first.
    assert "and 3 more" in empty.reason
    assert empty.reason.count("“") == gaps.QUERY_EXAMPLES
    assert empty.evidence["task_ids"].startswith("9")
    assert found["search_known"].evidence["results"] == 10


def test_one_failed_query_is_named_in_the_title():
    (g,) = gaps.query_gaps("t", description=None, answered=4, failing=[qy(3, 0, 0, "odd words")])
    assert "odd words" in g.title
    assert "1 of 4 answered searches" in g.reason


def test_the_rephrase_seed_is_prefilled_with_the_newest_failed_words_and_a_boost_follows():
    (g,) = gaps.query_gaps(
        "t", description="d", answered=3, failing=[qy(8, 0, 0, "newest"), qy(2, 0, 0, "older")]
    )
    seed, boost = g.actions
    assert (seed.kind, seed.query, seed.topic) == ("seed_query", "newest", "t")
    assert boost.kind == "boost_topic"


def test_query_severity_rises_with_the_failing_share_and_stays_under_an_empty_topic():
    def sev(kind_yield, n, answered):
        failing = [qy(i, *kind_yield) for i in range(n)]
        (g,) = gaps.query_gaps("t", description=None, answered=answered, failing=failing)
        return g.severity

    assert sev((0, 0), 1, 20) < sev((0, 0), 10, 20) < sev((0, 0), 20, 20)
    # Found-nothing outranks found-only-known at the same share.
    assert sev((5, 0), 20, 20) < sev((0, 0), 20, 20)
    # An empty topic (coverage) and an operator's low grade outrank any of these.
    assert sev((0, 0), 20, 20) < cover(sources=0)["thin"].severity
    assert sev((0, 0), 20, 20) < 0.7


# -- search results off topic (P6-37) ----------------------------------------------


def test_off_topic_needs_enough_read_results_and_a_minority_on_topic():
    too_few = gaps.OFF_TOPIC_MIN - 1
    assert gaps.off_topic_gap("t", description=None, examined=too_few, on_topic=0) is None
    n = gaps.OFF_TOPIC_MIN * 2
    at_threshold = int(n * gaps.OFF_TOPIC_SHARE)
    assert gaps.off_topic_gap("t", description=None, examined=n, on_topic=at_threshold) is None
    g = gaps.off_topic_gap("t", description=None, examined=n, on_topic=at_threshold - 1)
    assert g is not None and g.evidence == {
        "examined": n,
        "on_topic": at_threshold - 1,
        "on_topic_share": round((at_threshold - 1) / n, 3),
    }


def test_off_topic_severity_is_worst_when_nothing_found_was_on_topic():
    none_on = gaps.off_topic_gap("t", description=None, examined=10, on_topic=0)
    some_on = gaps.off_topic_gap("t", description=None, examined=10, on_topic=4)
    assert none_on.severity > some_on.severity


def test_off_topic_offers_no_boost():
    """Boosting a topic whose searches land elsewhere spends more crawl elsewhere."""
    g = gaps.off_topic_gap("t", description="d", examined=10, on_topic=0)
    assert [a.kind for a in g.actions] == ["seed_query"]


# -- the runs directory (P6-37) ---------------------------------------------------


def test_runs_dir_prefers_the_environment_and_ignores_a_blank_one(monkeypatch, tmp_path):
    from meridian_core.questionset import RUNS_DIR_ENV, runs_dir

    monkeypatch.setenv(RUNS_DIR_ENV, str(tmp_path))
    assert runs_dir(tmp_path / "default") == tmp_path
    assert gaps.runs_dir() == tmp_path
    monkeypatch.setenv(RUNS_DIR_ENV, "   ")
    assert runs_dir(tmp_path / "default") == tmp_path / "default"
    monkeypatch.delenv(RUNS_DIR_ENV)
    assert runs_dir(tmp_path / "default") == tmp_path / "default"


# -- routes ---------------------------------------------------------------------------


def anchor(topic, entity_id, support, name=None):
    return gaps.Anchor(topic, entity_id, name or f"node {entity_id}", support)


def check(*, cited=False, found=False, hops=None, similar=0, truncated=False):
    return gaps.RouteCheck(cited, found, hops, similar, truncated, 4)


def test_route_pairs_are_different_topics_most_cited_first_and_capped():
    anchors = [anchor("c", 3, 1), anchor("a", 1, 10), anchor("b", 2, 5), anchor("d", 1, 10)]
    pairs = gaps.route_pairs(anchors, limit=3)
    assert len(pairs) == 3
    # a and d share a node: one node heading two topics joins them trivially.
    assert all(p.entity_id != q.entity_id for p, q in pairs)
    assert [(p.topic, q.topic) for p, q in pairs] == [("a", "b"), ("b", "d"), ("a", "c")]
    assert gaps.route_pairs(anchors, limit=0) == []
    assert gaps.route_pairs([anchor("a", 1, 1)]) == []


def test_a_pair_joined_by_claims_is_not_a_gap():
    assert (
        gaps.route_gap(anchor("a", 1, 3), anchor("b", 2, 3), check(cited=True, found=True)) is None
    )


def test_no_route_outranks_a_route_through_resemblance():
    a, b = anchor("a", 1, 3, "first"), anchor("b", 2, 3, "second")
    none = gaps.route_gap(a, b, check())
    similar = gaps.route_gap(a, b, check(found=True, hops=3, similar=2))
    assert (none.kind, similar.kind) == ("route_none", "route_similar_only")
    assert none.severity > similar.severity
    assert "2 of them only because" in similar.reason
    assert none.id == similar.id == "route:a:b"
    # A route gap is a symptom; an empty topic is the gap.
    assert none.severity < cover(sources=0)["thin"].severity


def test_a_truncated_search_is_said_and_ranked_down():
    a, b = anchor("a", 1, 3), anchor("b", 2, 3)
    cut = gaps.route_gap(a, b, check(truncated=True))
    assert "work bound" in cut.reason
    assert cut.severity < gaps.route_gap(a, b, check()).severity


def test_the_route_seed_names_both_ends_and_fits_the_seed_payload():
    long_a, long_b = "alpha " * 30, "beta " * 30
    g = gaps.route_gap(anchor("a", 1, 3, long_a), anchor("b", 2, 3, long_b), check())
    (seed,) = g.actions
    assert seed.kind == "seed_query" and seed.topic == "a"
    assert len(seed.query) <= gaps.SEED_MAX
    GapSeed(topic=seed.topic, query=seed.query, gap_id=g.id)  # accepted as sent
    short = gaps.route_gap(anchor("a", 1, 3, "one"), anchor("b", 2, 3, "two"), check())
    assert short.actions[0].query == "one two"


def test_the_route_depth_is_one_the_route_search_accepts():
    from meridian_core.route import MAX_ROUTE_DEPTH

    assert 1 <= gaps.ROUTE_DEPTH <= MAX_ROUTE_DEPTH
