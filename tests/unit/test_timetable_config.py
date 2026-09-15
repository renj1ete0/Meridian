"""The shipped timetable names things that exist (tasks P5-06, P5-02).

`config/schedule.yaml` is seeded into `scheduled_jobs` at first boot and run as
``python -m <module>``. A row naming a module that does not exist, or one with no
entry point, fails at whatever hour it was scheduled for — as a `last_error`
string in a table nobody is watching, on a machine that is otherwise working.
The scheduler backs a failing job off and carries on, so the first symptom is
that something quietly never ran.

Cheap to check here, and the check is the whole point: these names are strings in
a YAML file, so nothing else in the toolchain looks at them at all.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
JOBS = yaml.safe_load((REPO / "config" / "schedule.yaml").read_text())["jobs"]


def test_the_timetable_is_not_empty() -> None:
    # Guards every parametrised test below: an empty list makes them all vacuous
    # and the file would have to be unparseable for anyone to notice.
    assert len(JOBS) >= 4


@pytest.mark.parametrize("job", JOBS, ids=lambda job: job["name"])
def test_every_job_runs_a_module_that_exists(job: dict) -> None:
    module = importlib.import_module(job["module"])

    assert callable(getattr(module, "main", None)), (
        f"{job['module']} has no main(); `python -m` would import it and exit 0, "
        "which the scheduler records as a successful run"
    )


@pytest.mark.parametrize("job", JOBS, ids=lambda job: job["name"])
def test_every_job_has_a_usable_interval(job: dict) -> None:
    # A zero or negative interval reschedules the job into the past on every
    # settle, which is a busy loop wearing a timetable's clothes.
    assert int(job["interval_seconds"]) > 0


@pytest.mark.parametrize("job", JOBS, ids=lambda job: job["name"])
def test_arguments_are_a_list_of_strings(job: dict) -> None:
    # `args` is passed to `create_subprocess_exec` as separate argv entries with
    # no shell. A bare string would be spread one character per argument, which
    # is the sort of failure that looks like the module rejecting its own flags.
    args = job.get("args")
    assert args is None or (isinstance(args, list) and all(isinstance(a, str) for a in args))


def test_the_names_are_unique() -> None:
    # `scheduled_jobs.name` is UNIQUE, and `seed_schedule` skips a name it
    # already has — so a duplicate here silently seeds only the first of the two.
    names = [job["name"] for job in JOBS]
    assert len(names) == len(set(names))


def test_no_job_deletes_anything_unattended() -> None:
    # The retention sweep is the only pass that destroys something a re-crawl
    # cannot reproduce. It is on the timetable to *report*, and `--apply` here
    # would make it delete on a timer with nobody having read the report.
    sweep = [job for job in JOBS if job["module"] == "worker.sweep"]

    assert sweep, "the sweep dropped off the timetable"
    assert all("--apply" not in (job.get("args") or []) for job in sweep)
