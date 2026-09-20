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
#: declared, with a reason, rather than quietly passing.
#:
#: Empty since `P1-37` — `build_and_push.sh` was the last one. Kept rather than
#: deleted because the mechanism is the point: the next Makefile target added
#: ahead of its script has somewhere to be declared, and
#: `test_the_unwritten_list_does_not_outlive_its_scripts` is what stops the
#: declaration becoming permanent.
UNWRITTEN: dict[str, str] = {}


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


@pytest.mark.parametrize("script", sorted(s for s in referenced_scripts() if (REPO / s).exists()))
def test_referenced_scripts_are_executable(script: str) -> None:
    """`make` invokes them as `./scripts/x.sh`, not as `bash scripts/x.sh`, so a
    missing execute bit is a "Permission denied" that reads as a file-ownership
    problem rather than a committed mode."""
    mode = (REPO / script).stat().st_mode
    assert mode & stat.S_IXUSR, f"{script} is not executable"


@pytest.mark.parametrize(
    "script",
    sorted(str(p.relative_to(REPO)) for p in (REPO / "scripts").glob("*.sh")),
)
def test_shell_scripts_parse(script: str) -> None:
    """`bash -n`. These run rarely and at the worst possible moment — a restore
    after a failure, a snapshot after a two-day crawl — so a syntax error in a
    branch nobody exercises is found at exactly the wrong time."""
    result = subprocess.run(["bash", "-n", str(REPO / script)], capture_output=True, text=True)
    assert result.returncode == 0, f"{script}: {result.stderr.strip()}"


# --------------------------------------------------------------------------
# The release script (task P1-37, scaffold §5)
# --------------------------------------------------------------------------

BUILD_AND_PUSH = REPO / "scripts/build_and_push.sh"


def build_script() -> str:
    return BUILD_AND_PUSH.read_text()


def test_the_release_script_never_tags_latest() -> None:
    """Scaffold §5 is explicit, and gives the reason: pinning the SHA in
    `docker-compose.yml` means a bad build does not roll out on the next
    restart, and rollback is a one-line edit. `latest` in an unattended system
    removes exactly the control you wanted — and it is a one-word change that
    looks like a convenience."""
    code = re.sub(r"^\s*#.*$", "", build_script(), flags=re.MULTILINE)

    assert ":latest" not in code
    assert "latest" not in re.findall(r"-t\s+(\S+)", code)


def test_the_release_script_refuses_a_dirty_tree() -> None:
    """The property that makes the SHA tag mean anything. Without it the tag
    names a commit whose code is not what was built, and that is discovered
    while rolling back."""
    code = build_script()

    assert "git status --porcelain" in code
    assert "--platform" in code, "a multi-arch manifest is the point of the script"


def test_the_release_script_covers_every_application_image() -> None:
    """Drift between the script and compose.

    `docker-compose.yml` is where an application service is added, and it is
    edited by hand. A service added there and not here is one that never gets
    built for arm64 — which shows up as a service that will not start on the
    Pi, days later, with nothing pointing at the cause.

    Third-party services are excluded by construction: they carry `image:` from
    an upstream registry rather than `build:`, and scaffold §5 notes they all
    publish multi-arch manifests already.
    """
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())

    built_by_compose = {
        name
        for name, service in compose["services"].items()
        if isinstance(service, dict) and "build" in service
    }
    # `crawl4ai` is built from a pinned upstream base for hardening (`P1-26`)
    # and is not one of ours; `embedder` shares the worker image rather than
    # having one of its own.
    built_by_compose -= {"crawl4ai", "embedder"}

    named_by_script = set(re.findall(r'^\s*"(\w+)\|', build_script(), flags=re.MULTILINE))

    missing = built_by_compose - named_by_script
    assert not missing, f"compose builds these and the release script does not: {sorted(missing)}"
