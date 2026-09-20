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

import os
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
    # and is not one of ours. `embedder` and `modelfetch` are the worker image
    # under a different command — one copy of 2.3 GB of weights is the whole
    # design (`P2-17`, `B-14`) — so neither is a separate thing to push.
    built_by_compose -= {"crawl4ai", "embedder", "modelfetch"}

    named_by_script = set(re.findall(r'^\s*"(\w+)\|', build_script(), flags=re.MULTILINE))

    missing = built_by_compose - named_by_script
    assert not missing, f"compose builds these and the release script does not: {sorted(missing)}"


# --------------------------------------------------------------------------
# The preflight check (task B-08, README "Minimum requirements")
# --------------------------------------------------------------------------

PREFLIGHT = REPO / "scripts/preflight.sh"


def readme_minimums() -> dict[str, int]:
    """The numbers out of the README's requirements table.

    Parsed rather than retyped, because the whole point of the test below is
    that two copies of a number drift. A row reads:

        | Memory | 8 GB | 16 GB or more |
    """
    rows = re.findall(
        r"^\|\s*(Memory|Storage|CPU)\s*\|\s*([\d.]+)\s*(?:GB|cores)",
        (REPO / "README.md").read_text(),
        flags=re.MULTILINE,
    )
    return {label.lower(): int(float(value)) for label, value in rows}


def test_the_readme_table_parses() -> None:
    """Guard on the parse. A regex that matched nothing would make the drift
    test below vacuously true, which is how a drift test stops testing anything
    while still passing."""
    found = readme_minimums()

    assert set(found) == {"cpu", "memory", "storage"}, found
    assert all(v > 0 for v in found.values()), found


def test_preflight_checks_against_the_readme_numbers() -> None:
    """The script hardcodes the minimums, so they are a second copy.

    Someone revising the README's requirements — which is where a user reads
    them — would otherwise leave the script warning against the old figures, and
    nothing would say so. The README is the source of truth; this asserts the
    script agrees with it.
    """
    script = PREFLIGHT.read_text()
    declared = {
        name: int(value)
        for name, value in re.findall(r"^(MIN_\w+)=(\d+)", script, flags=re.MULTILINE)
    }
    readme = readme_minimums()

    assert declared["MIN_CORES"] == readme["cpu"]
    assert declared["MIN_MEM_GB"] == readme["memory"]
    assert declared["MIN_DISK_GB"] == readme["storage"]


def test_preflight_only_fails_for_things_that_actually_block_a_start() -> None:
    """Warnings must not exit non-zero.

    `make quickstart` gates on this script, and the gate has to be about whether
    the stack *can* start — not about whether the machine matches a table. A
    person running a 500-document corpus on 4 GB is making a reasonable choice,
    and a preflight that refused would be substituting its judgement for theirs.
    """
    script = PREFLIGHT.read_text()

    # The only `exit 1` is the fatal verdict, and it is guarded by the fatal
    # counter rather than the warning counter.
    assert 'if [ "$fatal" -gt 0 ]; then' in script
    assert 'if [ "$warned" -gt 0 ]; then' in script

    warned_block = script.split('if [ "$warned" -gt 0 ]; then', 1)[1].split("fi", 1)[0]
    assert "exit 0" in warned_block, "a warning must not be a failure"
    assert "exit 1" not in warned_block


def test_preflight_emits_no_colour_when_nothing_is_watching() -> None:
    """Escape codes belong on a terminal. Piped to a file or a CI log they are
    noise in exactly the output somebody pastes into a bug report."""
    result = subprocess.run(
        ["bash", str(PREFLIGHT)],
        capture_output=True,
        text=True,
        cwd=REPO,
        env={**os.environ, "NO_COLOR": "1"},
    )

    assert "\033[" not in result.stdout


# --------------------------------------------------------------------------
# The tools image pins two versions by hand (tasks B-05, B-06)
# --------------------------------------------------------------------------

TOOLS_DOCKERFILE = REPO / "deploy/tools/Dockerfile"


def locked_version(package: str) -> str:
    """The version `uv.lock` resolves for a package."""
    match = re.search(
        rf'name = "{re.escape(package)}"\nversion = "([^"]+)"',
        (REPO / "uv.lock").read_text(),
    )
    assert match, f"{package} is not in uv.lock"
    return match.group(1)


def test_the_tools_image_pins_match_the_lockfile() -> None:
    """`deploy/tools/Dockerfile` installs `alembic` and `pyyaml` with
    `uv pip install`, pinned by hand.

    It has to: `alembic` is in the root project's `dev` group and `pyyaml` is a
    root dependency, and `uv sync --package meridian-core` — which is what keeps
    the worker's torch out of this image — installs neither. `uv sync` has no
    way to say "this workspace member plus the root's dev group".

    That makes the versions a second copy of the lockfile, so this asserts they
    agree. Without it a `uv lock --upgrade` moves one and the tool image quietly
    keeps building against the old one, which is exactly the drift the lockfile
    exists to prevent everywhere else.
    """
    dockerfile = TOOLS_DOCKERFILE.read_text()
    pinned = dict(re.findall(r"(\w+)==([\d.]+)", dockerfile))

    assert pinned, "no pinned versions found in the tools Dockerfile"
    for package, version in pinned.items():
        assert version == locked_version(package), (
            f"{package} is pinned at {version} in deploy/tools/Dockerfile "
            f"but uv.lock resolves {locked_version(package)}"
        )
