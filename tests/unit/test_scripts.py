"""Makefile targets point at scripts that exist (task P1-36).

This file exists because they did not. `make snapshot-corpus` is `P1-16`'s
stated deliverable — the 48h run's output *becomes* the dev corpus — and it
called a script nobody had written, so the target failed at the shell. The same
was true of three other targets.

Nothing catches that class of bug by reading code: the Makefile is valid, the
target is declared, and the failure only appears when someone runs it, which for
these targets is once, under time pressure, after a two-day crawl.

A grep, essentially. It is worth having anyway — the failure it prevents is
cheap to cause and expensive to discover.
"""

from __future__ import annotations

import re
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MAKEFILE = REPO / "Makefile"

#: Targets whose scripts are known to be unwritten, with the task that writes
#: them. Same idea as `test_drift.py`'s exempt sets: an omission has to be
#: declared, with a reason, rather than quietly passing. When `P1-37` lands, the
#: entry is removed and this test is what says so.
UNWRITTEN = {
    # `build_and_push.sh` is the multi-arch release path. A first deploy can
    # build on the server instead, so it is not on the critical path — but it
    # should exist before the stack is something anyone would rather not
    # rebuild in place.
    "scripts/build_and_push.sh": "P1-37",
}


def referenced_scripts() -> set[str]:
    """Every `./scripts/x.sh` the Makefile invokes."""
    return set(re.findall(r"\./(scripts/[\w./-]+\.sh)", MAKEFILE.read_text()))


def test_the_makefile_actually_calls_some_scripts() -> None:
    """Guard on the parse itself. A regex that silently matched nothing would
    make every assertion below vacuously true."""
    assert len(referenced_scripts()) >= 4


def test_every_referenced_script_exists() -> None:
    missing = {
        script
        for script in referenced_scripts()
        if not (REPO / script).exists() and script not in UNWRITTEN
    }
    assert not missing, f"Makefile targets calling scripts that do not exist: {sorted(missing)}"


def test_the_unwritten_list_does_not_outlive_its_scripts() -> None:
    """The other direction. A script that gets written while its exemption stays
    turns this file into documentation of a problem that no longer exists, and
    the next real omission hides among the stale entries."""
    stale = {script for script in UNWRITTEN if (REPO / script).exists()}
    assert not stale, f"written, but still listed as unwritten: {sorted(stale)} — remove them"


@pytest.mark.parametrize(
    "script", sorted(s for s in referenced_scripts() if (REPO / s).exists())
)
def test_referenced_scripts_are_executable(script: str) -> None:
    """`make` invokes them as `./scripts/x.sh`, not as `bash scripts/x.sh`, so a
    missing execute bit is a "Permission denied" that reads as a file-ownership
    problem rather than a committed mode."""
    mode = (REPO / script).stat().st_mode
    assert mode & stat.S_IXUSR, f"{script} is not executable"


@pytest.mark.parametrize(
    "script",
    sorted(
        str(p.relative_to(REPO))
        for p in (REPO / "scripts").glob("*.sh")
    ),
)
def test_shell_scripts_parse(script: str) -> None:
    """`bash -n`. These run rarely and at the worst possible moment — a restore
    after a failure, a snapshot after a two-day crawl — so a syntax error in a
    branch nobody exercises is found at exactly the wrong time."""
    result = subprocess.run(
        ["bash", "-n", str(REPO / script)], capture_output=True, text=True
    )
    assert result.returncode == 0, f"{script}: {result.stderr.strip()}"
