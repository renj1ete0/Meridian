"""The jobs reference agrees with the timetable it describes.

`config/schedule.yaml` seeds the timetable and `docs/reference/scheduled-jobs.md` is where an
operator reads what each row does. A job added to one and not the other is a job nobody can
look up (`runs-report` went undocumented for a release this way).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SECONDS = {"min": 60, "h": 3600, "d": 86400}


def documented() -> dict[str, dict[str, str]]:
    rows = {}
    text = (REPO / "docs" / "reference" / "scheduled-jobs.md").read_text()
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and re.fullmatch(r"`[\w-]+`", cells[0]):
            rows[cells[0].strip("`")] = {
                "command": cells[1].strip("`"),
                "every": cells[2],
                "seeded": cells[3],
            }
    return rows


def timetable() -> list[dict]:
    return yaml.safe_load((REPO / "config" / "schedule.yaml").read_text())["jobs"]


def test_every_job_has_exactly_one_row_and_every_row_a_job() -> None:
    assert sorted(documented()) == sorted(j["name"] for j in timetable())


def test_each_row_states_the_jobs_command_interval_and_seeded_state() -> None:
    rows = documented()
    for job in timetable():
        row = rows[job["name"]]
        assert row["command"] == " ".join([job["module"], *job.get("args", [])]), job["name"]
        number, unit = row["every"].split()
        assert int(number) * SECONDS[unit] == job["interval_seconds"], job["name"]
        assert row["seeded"] == ("on" if job["enabled"] else "off"), job["name"]
