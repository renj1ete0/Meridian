"""The graph audit's measure and its timetable row (task `P7-10`, spec §14.1).

Precision over thirty grades is a range, not a number, so the interval is checked against
values worked by hand. The timetable row lives in two places (the seed file and the migration
that reaches deployments seeded before it), and the two are held to each other.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
import yaml

from meridian_core.graphaudit import GRADES, KINDS, summary, tally, wilson
from meridian_core.models.graphaudit import AUDIT_GRADE, AUDIT_KIND
from meridian_core.permissions import BY_NAME, PROFILE_PERMISSIONS
from meridian_core.schemas.graphaudit import AuditDrawRead, AuditGrade, AuditSampleRead

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_nothing_judged_has_no_interval() -> None:
    assert wilson(0, 0) is None


def test_thirty_of_thirty_is_not_certainty() -> None:
    """Worked by hand: the plain interval collapses to [1, 1] here; Wilson does not."""
    low, high = wilson(30, 30)
    assert low == pytest.approx(0.8865, abs=1e-3)
    assert high == 1.0


def test_half_is_symmetric_and_narrows_with_more_grades() -> None:
    low, high = wilson(15, 30)
    assert 0.5 - low == pytest.approx(high - 0.5)
    wide = high - low
    low, high = wilson(150, 300)
    assert high - low < wide


@pytest.mark.parametrize(("correct", "judged"), [(31, 30), (-1, 5)])
def test_a_count_that_is_not_a_proportion_is_refused(correct: int, judged: int) -> None:
    with pytest.raises(ValueError, match="not a proportion"):
        wilson(correct, judged)


def test_precision_counts_correct_against_wrong_and_leaves_unsure_out() -> None:
    t = tally(["correct", "correct", "correct", "wrong", "unsure", None, None])
    assert (t.drawn, t.graded, t.correct, t.wrong, t.unsure) == (7, 5, 3, 1, 1)
    assert t.precision == pytest.approx(0.75)
    assert (t.low, t.high) == pytest.approx(wilson(3, 4))


def test_only_unsure_has_no_precision_yet() -> None:
    t = tally(["unsure", None])
    assert t.precision is None and t.low is None and t.graded == 1


def test_a_value_that_is_not_a_grade_is_refused() -> None:
    with pytest.raises(ValueError, match="not a grade"):
        tally(["correct", "maybe"])


def test_the_code_the_column_and_the_api_name_the_same_grades_and_kinds() -> None:
    """Drift: the CHECK, the module's constants and the request schema agree."""
    assert tuple(AUDIT_GRADE.enums) == GRADES
    assert tuple(AUDIT_KIND.enums) == KINDS
    assert set(AuditGrade.model_fields["grade"].annotation.__args__) == set(GRADES)
    assert set(AuditSampleRead.model_fields["kind"].annotation.__args__) == set(KINDS)


def test_grading_is_a_permission_of_its_own_held_by_operators() -> None:
    assert BY_NAME["graph.audit"].group == "Steering the crawl"
    assert "graph.audit" in PROFILE_PERMISSIONS["operator"]
    assert "graph.audit" not in PROFILE_PERMISSIONS["analyst"]


def _migration():
    path = next((ROOT / "migrations" / "versions").glob("*p710a1c2d3e4*.py"))
    spec = importlib.util.spec_from_file_location("p710", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_seeds_the_row_the_timetable_ships() -> None:
    """Drift: a deployment seeded before the job gets it from the migration, a fresh one from
    `config/schedule.yaml`. Two different rows would mean two different audits."""
    jobs = yaml.safe_load((ROOT / "config" / "schedule.yaml").read_text())["jobs"]
    shipped = next(job for job in jobs if job["name"] == "graph-audit")
    name, module, args, interval = _migration().JOB
    assert (name, module, args, interval) == (
        shipped["name"],
        shipped["module"],
        shipped.get("args"),
        shipped["interval_seconds"],
    )
    assert shipped["enabled"] is True


def test_the_job_never_runs_with_a_flag_that_could_change_the_graph() -> None:
    """The draw samples and measures; nothing on its row asks it to do more."""
    jobs = yaml.safe_load((ROOT / "config" / "schedule.yaml").read_text())["jobs"]
    shipped = next(job for job in jobs if job["name"] == "graph-audit")
    assert not shipped.get("args")


def _draw(**over) -> AuditDrawRead:
    graded = tally(["correct"] * 9 + ["wrong"])
    empty = tally([])
    values = {
        "draw_id": 1,
        "drawn_at": "2001-04-01T00:00:00+00:00",
        "window_start": "2001-03-01T00:00:00+00:00",
        "window_end": "2001-04-01T00:00:00+00:00",
        "seed": "s",
        "drawn_by": "scheduler",
        "edge_population": 120,
        "merge_population": 4,
        "merges_reversed": 0,
        "edges": graded,
        "merges": empty,
        "by_producer": [],
    }
    return AuditDrawRead(**(values | over))


def test_the_message_says_what_to_grade_and_how_the_last_draw_came_out() -> None:
    current = _draw(edges=tally([None] * 30), merges=tally([None] * 4), merges_reversed=2)
    text = summary(current, _draw())
    lines = text.splitlines()
    assert lines[0] == "Graph audit: 30 relations and 4 merges to grade"
    assert "since 2001-03-01" in text
    assert "2 merges in the window were already reversed" in text
    assert "relations 90%" in text and "of 10 judged" in text
    assert "merges not graded" in text


def test_the_first_message_has_no_last_draw() -> None:
    assert "last draw" not in summary(_draw(), None)
