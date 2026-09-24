"""The question-set runs directory, as both compose files mount it (task P6-37).

Gaps reads the newest run file from `MERIDIAN_EVAL_RUNS_DIR`; the runner, in
`tools`, writes there. That is four things that must name one directory — the
API's mount, the API's variable, the runner's mount and the runner's variable —
in two files edited by hand. Any one drifting fails silently: the API reports
the source "unavailable" and a run lands where nobody reads it. Hence drift
tests, across services and across the two files, derived from the files
themselves rather than a hardcoded path.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from meridian_core.questionset import RUNS_DIR_ENV

yaml = pytest.importorskip("yaml")

REPO = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_FILES = [REPO / "docker-compose.yml", REPO / "docker-compose.local.yml"]

#: `${DATA_ROOT:-<default>}/<sub>` — the part after DATA_ROOT is what must agree.
DATA_ROOT = re.compile(r"^\$\{DATA_ROOT:-[^}]+\}/(?P<sub>[^:]+)$")


def services(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text())["services"]


def env_of(service: dict, name: str) -> str | None:
    env = service.get("environment") or {}
    if isinstance(env, list):
        env = dict(str(item).split("=", 1) for item in env if "=" in str(item))
    value = env.get(name)
    return None if value is None else str(value)


def mounts_at(service: dict, target: str) -> list[tuple[str, str]]:
    """(host subpath under DATA_ROOT, mode) for each mount at ``target``."""
    out = []
    for volume in service.get("volumes") or []:
        parts = str(volume).split(":")
        # `${DATA_ROOT:-/srv/meridian}/x:/t:ro` splits on the default's colon too.
        if len(parts) < 2:
            continue
        mode = parts[-1] if parts[-1] in ("ro", "rw") else "rw"
        tail = parts[:-1] if parts[-1] in ("ro", "rw") else parts
        if tail[-1] != target:
            continue
        host = ":".join(tail[:-1])
        match = DATA_ROOT.match(host)
        out.append((match.group("sub") if match else host, mode))
    return out


def runs_mount(path: pathlib.Path, name: str) -> tuple[str, str, str]:
    """(container path, host subpath, mode) for one service's runs mount."""
    service = services(path)[name]
    target = env_of(service, RUNS_DIR_ENV)
    assert target, f"{path.name}: {name} does not set {RUNS_DIR_ENV}"
    found = mounts_at(service, target)
    assert len(found) == 1, (
        f"{path.name}: {name} sets {RUNS_DIR_ENV}={target} but mounts {found or 'nothing'} there"
    )
    return (target, *found[0])


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_the_api_mounts_the_directory_its_variable_names_read_only(path):
    target, sub, mode = runs_mount(path, "api")
    assert mode == "ro", f"{path.name}: the API only reads runs; {target} is mounted {mode}"
    # Under DATA_ROOT like every other data directory, not a path into the
    # checkout — a deployment has no checkout (mounts_at returns the raw host
    # path when the DATA_ROOT form does not match).
    assert not sub.startswith(("/", ".", "$")), f"{path.name}: {sub} is not under DATA_ROOT"


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_the_runner_writes_where_the_api_reads(path):
    api = runs_mount(path, "api")
    tools = runs_mount(path, "tools")
    assert tools[:2] == api[:2], f"{path.name}: tools {tools} and api {api} disagree"
    assert tools[2] == "rw", f"{path.name}: the runner cannot write a read-only mount"


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_datadirs_chowns_the_runs_directory(path):
    """docs/handover.md §3: Docker creates a missing bind source as root."""
    target, sub, _ = runs_mount(path, "api")
    datadirs = services(path)["datadirs"]
    assert (sub, "rw") in mounts_at(datadirs, target), f"{path.name}: datadirs does not mount it"
    command = " ".join(datadirs["command"]) if isinstance(datadirs["command"], list) else ""
    assert target in command.split("chown", 1)[1], f"{path.name}: datadirs does not chown it"


def test_both_compose_files_mount_the_same_directory_at_the_same_path():
    prod, local = (runs_mount(path, "api") for path in COMPOSE_FILES)
    assert prod == local


def test_the_tools_image_carries_what_the_runner_reads():
    """`scripts/run_question_set.py` defaults to `eval/questions.yaml` and
    `VERSION` next to `scripts/`; an image without them fails on a server with
    `No such file`, which reads like a typo in the docs."""
    text = (REPO / "deploy/tools/Dockerfile").read_text()
    copied = {m.group(1) for m in re.finditer(r"^COPY\s+(\S+)", text, re.MULTILINE)}
    assert {"scripts/", "VERSION", "eval/questions.yaml"} <= copied


@pytest.mark.parametrize(
    "volume",
    [
        "${DATA_ROOT:-/srv/meridian}/eval-runs:/elsewhere:ro",
        "${DATA_ROOT:-/srv/meridian}/eval-runs",
    ],
)
def test_mounts_at_refuses_a_mount_at_another_target(volume):
    """Guard on the parser: a mount elsewhere, or a bare volume, is not a match."""
    assert mounts_at({"volumes": [volume]}, "/data/eval-runs") == []
