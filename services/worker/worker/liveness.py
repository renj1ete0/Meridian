"""A heartbeat the container runtime can see (task P5-08, spec §13.4).

The loop touches a file each time round, and the container's healthcheck compares its
mtime to now, so a process that is running but wedged is restarted. A file under the
tmpfs `/tmp`, not a port. See docs/features/operations.md#liveness.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from meridian_core.logging import get_logger

log = get_logger(__name__)

DEFAULT_PATH = "/tmp/meridian-worker.alive"

#: How old the heartbeat may be before the worker is considered wedged. Generous: one
#: fetch can take a minute against a slow origin.
DEFAULT_MAX_AGE_S = 300


def heartbeat_path() -> Path:
    return Path(os.environ.get("MERIDIAN_LIVENESS_PATH", DEFAULT_PATH))


def beat(path: Path | None = None) -> None:
    """Record that the loop went round. Never raises.

    A liveness file that cannot be written is a monitoring problem, and taking
    the crawl down over one would be the monitoring causing the outage it exists
    to detect.
    """
    target = path or heartbeat_path()
    try:
        target.touch()
    except OSError as exc:  # pragma: no cover - a read-only or missing /tmp
        log.warning("could not write the liveness file", extra={"reason": str(exc)})


def age_seconds(path: Path | None = None, *, now: float | None = None) -> float | None:
    """How long since the last beat, or None if there has never been one."""
    target = path or heartbeat_path()
    try:
        return (now or time.time()) - target.stat().st_mtime
    except OSError:
        return None


def is_alive(path: Path | None = None, *, max_age_s: int = DEFAULT_MAX_AGE_S) -> bool:
    """Whether the loop has gone round recently enough.

    A missing file is not alive; the healthcheck's `start_period` covers start-up.
    """
    age = age_seconds(path)
    return age is not None and age <= max_age_s


def main() -> int:
    """Entry point for the container healthcheck: ``python -m worker.liveness``."""
    age = age_seconds()
    if age is None:
        print("no heartbeat yet")
        return 1
    if age > DEFAULT_MAX_AGE_S:
        print(f"heartbeat is {age:.0f}s old")
        return 1
    print(f"alive, {age:.0f}s since the last iteration")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
