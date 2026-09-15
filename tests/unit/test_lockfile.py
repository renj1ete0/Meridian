"""The lockfile tracks the version it locks (task P1-37).

Both Dockerfiles build with `uv sync --frozen`, which refuses to run when
`uv.lock` disagrees with the `pyproject.toml` files. Every release bumps four
workspace versions, and `uv.lock` records all four — so a bump committed without
re-locking makes the image unbuildable.

It is a silent break in the worst way: nothing in the test suite touches it,
`uv run` re-locks in place so local work is unaffected, and the failure surfaces
at `make build-push` — which is to say, at deploy time, from a commit that passed
everything.

This checks the version correspondence rather than shelling out to
`uv lock --check`, deliberately. The full check resolves the dependency graph and
is the stronger assertion, but it is also the slow one and the one that fails for
reasons that have nothing to do with this repo. Version drift is the failure this
project actually produces — it had gone stale across a dozen commits before
anyone looked — and catching it costs a file read.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: Every workspace member, and the root. Read from the workspace declaration
#: rather than listed here: a service added to `members` and forgotten here
#: would be the one whose version goes stale.
ROOT = tomllib.loads((REPO / "pyproject.toml").read_text())
MEMBERS = [REPO] + [
    path
    for pattern in ROOT["tool"]["uv"]["workspace"]["members"]
    for path in sorted(REPO.glob(pattern))
    if (path / "pyproject.toml").exists()
]


def declared_version(package_dir: Path) -> str:
    return tomllib.loads((package_dir / "pyproject.toml").read_text())["project"]["version"]


def locked_versions() -> dict[str, str]:
    """Package name → version, for the entries `uv.lock` marks editable.

    Editable is what identifies a workspace member: every other entry is a
    third-party package whose version is not ours to keep in step.
    """
    text = (REPO / "uv.lock").read_text()
    found: dict[str, str] = {}
    for block in text.split("[[package]]"):
        name = re.search(r'^name = "([^"]+)"', block, re.M)
        version = re.search(r'^version = "([^"]+)"', block, re.M)
        if name and version and "editable = " in block:
            found[name.group(1)] = version.group(1)
    return found


def test_the_workspace_has_members_to_check() -> None:
    # Guards every test below. A glob that stops matching makes them vacuous,
    # and nothing about a passing suite would say so.
    assert len(MEMBERS) >= 4


def test_every_member_declares_the_root_version() -> None:
    # `VERSION` is the single source of truth and the pyprojects mirror it
    # (AGENTS.md). A service left behind ships an image whose reported version is
    # not the one that was built.
    expected = (REPO / "VERSION").read_text().strip()

    assert {path.name: declared_version(path) for path in MEMBERS} == {
        path.name: expected for path in MEMBERS
    }


def test_the_lockfile_records_the_editable_packages() -> None:
    assert len(locked_versions()) >= 3, "uv.lock no longer marks workspace members editable"


def test_the_lockfile_agrees_with_the_pyprojects() -> None:
    # The one that matters: `uv sync --frozen` refuses on exactly this
    # disagreement, and the refusal happens in the Docker build rather than here.
    locked = locked_versions()
    declared = {
        tomllib.loads((path / "pyproject.toml").read_text())["project"]["name"]: declared_version(
            path
        )
        for path in MEMBERS
    }
    drifted = {
        name: (version, locked[name])
        for name, version in declared.items()
        if name in locked and locked[name] != version
    }

    assert drifted == {}, f"uv.lock is stale — run `uv lock`. Drifted: {drifted}"


@pytest.mark.parametrize("service", ["worker", "api"])
def test_the_image_builds_from_the_lockfile(service: str) -> None:
    # The reason the tests above exist at all. If a Dockerfile stopped passing
    # `--frozen` they would still be worth having, but the urgency would be gone
    # — and this is what notices that the premise changed.
    dockerfile = (REPO / "services" / service / "Dockerfile").read_text()

    assert "--frozen" in dockerfile
