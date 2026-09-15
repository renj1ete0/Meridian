"""A heartbeat the container runtime can see (task P5-08, spec §13.4).

§13.4 asks for supervision that survives crashes unattended, and
`restart: unless-stopped` already handles a worker that *exits*. It does nothing
at all for one that is still running and no longer working — a wedged fetch, a
connection pool that never recovers, a lane stuck on a lock. From outside, that
process is indistinguishable from a healthy one that happens to be busy.

So the loop touches a file each time round, and the container's healthcheck asks
how old it is. A worker that has not completed an iteration in minutes is one
Docker can restart, which is the whole point of declaring a healthcheck at all.

**A file rather than a port.** The worker serves no HTTP and should not start
doing so to be observable — a port is a surface, and this needs to be readable
by a process that has no credentials. `/tmp` is a tmpfs in the container
(`P1-30` runs it read-only with `tmpfs: [/tmp]`), so the file cannot survive a
restart and cannot be mistaken for an old one.

**Staleness is measured, not asserted.** The check compares mtime to now rather
than trusting the file's existence, because a worker that died holding the file
leaves it behind — and a liveness probe that passes on a dead process is worse
than no probe, since it also suppresses the restart.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from meridian_core.logging import get_logger

log = get_logger(__name__)

DEFAULT_PATH = "/tmp/meridian-worker.alive"

#: How old the heartbeat may be before the worker is considered wedged.
#: Generous: a single fetch can legitimately take a minute against a slow
#: origin, and a probe that fires during normal work trains an operator to
#: ignore it — the same failure `P5-07` designs against.
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

    A missing file is *not* alive. During startup that is briefly true and the
    healthcheck's `start_period` covers it; after that, a missing file means the
    loop never reached its first iteration, which is exactly the state worth
    restarting.
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
