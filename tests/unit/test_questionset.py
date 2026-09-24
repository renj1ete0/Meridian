"""The question-set core (task P2-22, spec §14.1; rules from eval/README.md)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import yaml

from meridian_core.questionset import (
    HEURISTIC,
    SCALE,
    HitView,
    Question,
    QuestionSet,
    QuestionSetError,
    attach_comparison,
    banner,
    compare,
    item_record,
    load,
    next_run_path,
    previous_run,
    propose,
    read_run,
)

REAL = Path(__file__).resolve().parents[2] / "eval" / "questions.yaml"


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "q.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def _q(**over) -> Question:
    base = dict(
        id="X1",
        kind="lookup",
        question="q",
        topics=("walkability",),
        concepts=("sheltered walkway coverage", "programme target"),
        expected_tiers=("government",),
        reviewed=False,
    )
    base.update(over)
    return Question(**base)


# -- the real file -----------------------------------------------------------


def test_the_real_set_loads_and_every_id_is_unique():
    """Drift: the runner must read the file the operator edits, not a copy."""
    qs = load(REAL)
    raw = yaml.safe_load(REAL.read_text())
    assert [q.id for q in qs.questions] == [e["id"] for e in raw["questions"]]
    assert qs.set_version == raw["set_version"]


def test_the_real_set_reports_its_review_state_from_the_file():
    """An unreviewed set is a draft; the count comes from the file, not a constant."""
    qs = load(REAL)
    raw = yaml.safe_load(REAL.read_text())
    expected = [e["id"] for e in raw["questions"] if e.get("reviewed") is not True]
    assert qs.unreviewed == expected
    assert qs.is_draft == (bool(expected) or raw.get("status") == "draft")


def test_every_real_item_topic_is_a_configured_topic():
    """Drift against config/topics.yaml: an item naming a topic that does not
    exist can never match a hit's labels and would score as a false miss."""
    topics_file = REAL.parents[1] / "config" / "topics.yaml"
    configured = yaml.safe_load(topics_file.read_text())
    names = {
        t["topic"] if isinstance(t, dict) else t for t in (configured.get("topics") or configured)
    }
    for q in load(REAL).questions:
        assert set(q.topics) <= names, q.id


# -- rejection ---------------------------------------------------------------


def test_refuses_a_file_without_questions(tmp_path):
    with pytest.raises(QuestionSetError):
        load(_write(tmp_path, {"set_version": 1}))


def test_refuses_a_file_without_a_version(tmp_path):
    with pytest.raises(QuestionSetError, match="set_version"):
        load(_write(tmp_path, {"questions": [{"id": "A", "kind": "gap", "question": "x"}]}))


def test_refuses_duplicate_ids(tmp_path):
    item = {"id": "A", "kind": "gap", "question": "x"}
    with pytest.raises(QuestionSetError, match="duplicate"):
        load(_write(tmp_path, {"set_version": 1, "questions": [item, item]}))


def test_only_an_explicit_true_counts_as_reviewed(tmp_path):
    qs = load(
        _write(
            tmp_path,
            {
                "set_version": 1,
                "status": "frozen",
                "questions": [
                    {"id": "A", "kind": "gap", "question": "x", "reviewed": "yes"},
                    {"id": "B", "kind": "gap", "question": "x"},
                    {"id": "C", "kind": "gap", "question": "x", "reviewed": True},
                ],
            },
        )
    )
    assert qs.unreviewed == ["A", "B"]
    assert qs.is_draft


# -- the heuristic proposal --------------------------------------------------


def test_no_hits_proposes_zero():
    assert propose(_q(), []).grade == 0


def test_proposal_never_exceeds_two_for_answerable_kinds():
    """A 3 needs a reader; the heuristic only sees presence."""
    hits = [
        HitView("sheltered walkway coverage programme target", "government", ["walkability"])
    ] * 5
    p = propose(_q(), hits)
    assert p.grade == 2
    assert p.method == HEURISTIC


def test_right_words_from_the_wrong_tier_are_not_a_two():
    hits = [HitView("sheltered walkway coverage programme target", "informal", ["walkability"])]
    assert propose(_q(), hits).grade == 1


def test_gap_item_is_capped_at_one_and_penalised_for_on_topic_hits():
    gap = _q(kind="gap")
    assert propose(gap, []).grade == 1
    on_topic = [HitView("anything", "government", ["walkability"])]
    assert propose(gap, on_topic).grade == 0


# -- run records and comparison ---------------------------------------------


def test_a_run_item_keeps_proposal_and_operator_grade_apart():
    rec = item_record(_q(), propose(_q(), []), [])
    assert rec["operator"] is None
    assert rec["proposed"]["method"] == HEURISTIC


def _run(version, grades):
    return {
        "set_version": version,
        "items": [
            {"id": k, "proposed": {"grade": 3}, "operator": None if g is None else {"grade": g}}
            for k, g in grades.items()
        ],
    }


def test_compare_reads_operator_grades_only():
    """Proposals of 3 everywhere must not make an ungraded run look scored."""
    out = compare(_run(1, {"A": None}), _run(1, {"A": None}))
    assert out["graded"] is False
    assert out["mean_after"] is None


def test_compare_flags_a_two_point_drop():
    out = compare(_run(1, {"A": 3, "B": 2}), _run(1, {"A": 1, "B": 2}))
    rows = {r["id"]: r for r in out["items"]}
    assert rows["A"]["regression"] and rows["A"]["delta"] == -2
    assert not rows["B"]["regression"]


def test_compare_refuses_different_set_versions():
    with pytest.raises(QuestionSetError, match="not comparable"):
        compare(_run(1, {}), _run(2, {}))


# -- run files ----------------------------------------------------------------


@pytest.mark.parametrize("bad", [2.5, "three", True, 4, -1])
def test_read_run_refuses_grades_off_the_scale(tmp_path, bad):
    path = tmp_path / "r.yaml"
    path.write_text(yaml.safe_dump({"items": [{"id": "A", "operator": {"grade": bad}}]}))
    with pytest.raises(QuestionSetError, match="operator grade"):
        read_run(path)


def test_read_run_accepts_every_grade_on_the_scale_and_ungraded_items(tmp_path):
    items = [{"id": str(g), "operator": {"grade": g}} for g in SCALE]
    items.append({"id": "u", "operator": None})
    path = tmp_path / "r.yaml"
    path.write_text(yaml.safe_dump({"items": items}))
    assert len(read_run(path)["items"]) == len(SCALE) + 1


def test_next_run_path_never_overwrites_a_run(tmp_path):
    day = dt.date(2026, 9, 24)
    first = next_run_path(tmp_path, day)
    first.write_text("x")
    second = next_run_path(tmp_path, day)
    assert first.name == "2026-09-24.yaml" and second.name == "2026-09-24-2.yaml"
    second.write_text("x")
    assert previous_run(tmp_path) == second
    assert previous_run(tmp_path, excluding=second) == first


def test_attach_comparison_records_a_version_mismatch_instead_of_raising():
    run = attach_comparison(_run(2, {"A": None}), _run(1, {"A": 2}), "old.yaml")
    assert "not comparable" in run["comparison"]["note"]


def test_attach_comparison_says_when_nothing_is_graded():
    run = attach_comparison(_run(1, {"A": None}), _run(1, {"A": None}), "old.yaml")
    assert run["comparison"]["graded"] is False
    assert "not scores" in run["comparison"]["note"]


def test_banner_disappears_only_when_every_item_is_reviewed_and_the_set_is_not_draft():
    reviewed = QuestionSet(1, "frozen", (_q(reviewed=True),))
    assert banner(reviewed) is None
    assert banner(QuestionSet(1, "draft", (_q(reviewed=True),))) is not None
    assert "1 of 1" in banner(QuestionSet(1, "frozen", (_q(),)))
