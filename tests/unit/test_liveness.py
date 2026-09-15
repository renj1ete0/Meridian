"""The worker heartbeat (task P5-08, spec §13.4).

`restart: unless-stopped` covers a worker that exits. This covers the one that
does not: still running, no longer working, and indistinguishable from busy.

The test that matters is the stale one. A liveness probe that passes on a dead
process is worse than no probe at all, because it also suppresses the restart
that would have fixed it.
"""

from __future__ import annotations

import os
import time

import pytest

from worker.liveness import (
    DEFAULT_MAX_AGE_S,
    age_seconds,
    beat,
    heartbeat_path,
    is_alive,
)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "alive"


def test_a_fresh_beat_is_alive(path) -> None:
    beat(path)

    assert is_alive(path) is True
    assert age_seconds(path) < 5


def test_a_stale_beat_is_not_alive(path) -> None:
    """The whole point. A worker wedged inside a fetch keeps its process and
    stops its loop, and only the age of this file tells them apart."""
    beat(path)
    old = time.time() - (DEFAULT_MAX_AGE_S + 60)
    os.utime(path, (old, old))

    assert is_alive(path) is False


def test_a_missing_beat_is_not_alive(path) -> None:
    """Not "unknown, assume fine". During startup a missing file is briefly
    true and the healthcheck's `start_period` covers it; afterwards it means the
    loop never reached its first iteration, which is exactly the state worth
    restarting."""
    assert age_seconds(path) is None
    assert is_alive(path) is False


def test_an_unwritable_path_does_not_take_the_crawl_down(tmp_path) -> None:
    """A liveness file that cannot be written is a monitoring problem. Raising
    here would make the monitoring cause the outage it exists to detect."""
    beat(tmp_path / "no-such-directory" / "alive")  # must not raise


def test_the_path_is_configurable(monkeypatch, tmp_path) -> None:
    """`P1-30` runs the container read-only with a tmpfs at `/tmp`, and a
    developer running the worker natively has neither."""
    monkeypatch.setenv("MERIDIAN_LIVENESS_PATH", str(tmp_path / "custom"))

    assert heartbeat_path() == tmp_path / "custom"


def test_the_healthcheck_entry_point_reports_by_exit_code(monkeypatch, tmp_path) -> None:
    """Docker reads the exit code, not the output. A check that printed a
    problem and exited 0 would be a healthcheck that always passes."""
    from worker.liveness import main

    monkeypatch.setenv("MERIDIAN_LIVENESS_PATH", str(tmp_path / "beat"))
    assert main() == 1

    beat(tmp_path / "beat")
    assert main() == 0
