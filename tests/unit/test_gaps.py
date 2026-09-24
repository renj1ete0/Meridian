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

    emitted = {a.kind for a in gaps._seed_and_boost("t", None)} | {"open_search"}
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
    assert set(gaps.SOURCES) == {"topic-coverage", "search-yield", "question-set"}
    assert set(gaps.PENDING) == {"areas", "routes"}


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
