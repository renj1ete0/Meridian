"""One owner per pass (task `B-25`, §13.1, §6.1).

A module that runs as a compose service *and* as an enabled row in the
timetable has two owners. Both claim batches, both load weights, and they
compete for the CPU the crawl is already using — and neither is wrong from
where it is standing, so nothing reports it.

That is not hypothetical here. `worker.embed` was an hourly job; it is now a
service, and the row had to be disabled in the same change. The next pass to
graduate the same way will be added by somebody who does not remember this,
which is what this file is for.

It reads both sources rather than listing names: the compose file for what runs
continuously, `config/schedule.yaml` for what is scheduled, and fails when a
module appears in both.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def timetable() -> list[dict]:
    return yaml.safe_load((REPO / "config" / "schedule.yaml").read_text())["jobs"]


@pytest.fixture(scope="module")
def services() -> dict:
    return yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]


def modules_run_as_services(services: dict) -> dict[str, str]:
    """`{module: service name}` for every service running `python -m <module>`.

    Read off the command rather than matched by service name, because the two
    do not have to agree — and where they disagree is exactly where somebody
    would miss the collision by eye.
    """
    found: dict[str, str] = {}
    for name, definition in services.items():
        command = definition.get("command") or []
        if isinstance(command, str):
            command = command.split()
        if "-m" in command:
            module = command[command.index("-m") + 1]
            if module.startswith("worker."):
                found[module] = name
    return found


def test_the_sweep_finds_the_services_it_is_meant_to_check(services: dict) -> None:
    """A discovery test that discovers nothing passes over everything."""
    found = modules_run_as_services(services)

    assert "worker.embed" in found, f"expected the embedding service; found {sorted(found)}"
    assert len(found) >= 3, f"only found {sorted(found)}; the sweep is not reading the file"


def test_no_enabled_job_runs_a_module_that_is_already_a_service(
    timetable: list[dict], services: dict
) -> None:
    """The collision itself.

    `P5-06` claims with `SKIP LOCKED` and a lease, so the two would not corrupt
    anything — they would simply both be running, on a box where the crawl and
    the embedder are already matched for throughput with nothing to spare.
    """
    as_services = modules_run_as_services(services)

    clashes = [
        f"{job['name']} ({job['module']}) is also the {as_services[job['module']]} service"
        for job in timetable
        if job.get("enabled") and job["module"] in as_services
    ]

    assert not clashes, f"two owners for one pass: {clashes}"


def test_the_embedding_row_is_kept_rather_than_deleted(timetable: list[dict]) -> None:
    """Disabled, not gone. It records the cadence somebody would otherwise
    reinvent, and re-enabling it is how the pass runs where there is no
    service to run it."""
    row = next((job for job in timetable if job["name"] == "embed"), None)

    assert row is not None, "the row documents the fallback; deleting it loses that"
    assert row["enabled"] is False
