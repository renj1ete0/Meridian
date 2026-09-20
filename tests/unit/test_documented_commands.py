"""Documented `docker compose run` commands name an image that can run them
(task `B-17`).

`docs/setup.md` and `docs/deployment.md` both told an operator to migrate with

    docker compose run --rm worker alembic upgrade head
    docker compose run --rm worker python scripts/seed.py

and neither command can work. `alembic` is in the root project's `dev`
dependency group and every application image syncs `--no-dev`; the worker's
Dockerfile copies `packages/`, `services/` and `config/`, and never `scripts/`.
Both failed the first time anyone ran them, on the server, at the step where
the database is created.

Documentation rots quietly because nothing executes it. This does the next best
thing: for every `docker compose run` in the deployment docs, resolve the
service to the Dockerfile that builds it and check that what the command needs
is actually in there. Derived from the files on both sides, so a Dockerfile that
stops copying something fails here rather than on somebody's server.

What it cannot check is whether the command *succeeds* — only that the thing
being invoked is present. That is the difference between this and the smoke run
in `docs/deployment.md` §4, and it is worth being honest about.
"""

from __future__ import annotations

import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

REPO = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = REPO / "docker-compose.yml"
DOCS = [REPO / "docs/setup.md", REPO / "docs/deployment.md"]

#: `docker compose run [--rm] [-e FOO=bar ...] <service> [command...]`
#:
#: The command is optional and that matters: `docker compose run --rm datadirs`
#: takes its command from the compose file. A pattern that required one read
#: `--rm` as the service name and reported it missing, which is a test failing
#: about itself.
RUN = re.compile(
    r"docker compose run\s+(?:(?:--rm|-e\s+\S+)\s+)*"
    r"(?P<service>[a-z][a-z0-9_-]*)(?:\s+(?P<command>[^\n`]+))?"
)


def documented_runs() -> list[tuple[str, str, str]]:
    """(doc name, service, command) for every `compose run` in the docs."""
    found: list[tuple[str, str, str]] = []
    for path in DOCS:
        if not path.exists():
            continue
        for match in RUN.finditer(path.read_text()):
            command = (match.group("command") or "").split("#")[0].strip()
            found.append((path.name, match.group("service"), command))
    return found


def dockerfile_for(service: str) -> pathlib.Path | None:
    """The Dockerfile that builds a compose service, if it is built here."""
    spec = (yaml.safe_load(COMPOSE.read_text())["services"].get(service) or {}).get("build")
    if isinstance(spec, dict) and spec.get("dockerfile"):
        return REPO / str(spec["dockerfile"])
    if isinstance(spec, str):
        return REPO / spec / "Dockerfile"
    return None


def test_the_docs_document_some_commands() -> None:
    """Guard on the regex. If it matches nothing, every check below passes over
    an empty list — which is how a drift test stops being one."""
    runs = documented_runs()

    assert len(runs) > 5, runs
    assert any("alembic" in command for _, _, command in runs), (
        "the migration command is the one this test was written for"
    )


@pytest.mark.parametrize(
    "doc,service,command",
    documented_runs(),
    ids=lambda v: str(v).replace(" ", "-")[:60],
)
def test_the_service_exists(doc: str, service: str, command: str) -> None:
    services = yaml.safe_load(COMPOSE.read_text())["services"]

    assert service in services, f"{doc}: `docker compose run {service}` — no such service"


@pytest.mark.parametrize(
    "doc,service,command",
    documented_runs(),
    ids=lambda v: str(v).replace(" ", "-")[:60],
)
def test_the_image_carries_what_the_command_needs(
    doc: str, service: str, command: str
) -> None:
    """`alembic` has to be installed; `scripts/foo.py` has to be copied in.

    Both are readable off the Dockerfile, and both were the thing that was
    missing — a command that needs a file the image never copies fails with a
    `No such file or directory` that reads like a typo in the docs.
    """
    dockerfile = dockerfile_for(service)
    if dockerfile is None or not dockerfile.exists():
        pytest.skip(f"{service} is not built from a Dockerfile in this repo")
    text = dockerfile.read_text()

    if command.startswith("alembic"):
        assert "alembic" in text, (
            f"{doc}: `docker compose run {service} {command}` — "
            f"{dockerfile.relative_to(REPO)} never installs alembic"
        )

    path = re.match(r"python\s+(scripts/\S+)", command)
    if path:
        assert re.search(r"^COPY .*\bscripts/", text, re.MULTILINE), (
            f"{doc}: `docker compose run {service} {command}` — "
            f"{dockerfile.relative_to(REPO)} never copies scripts/"
        )

    module = re.match(r"python\s+-m\s+([a-z_]+)\.", command)
    if module:
        assert re.search(rf"^COPY .*\b{module.group(1)}\b", text, re.MULTILINE) or re.search(
            rf"^COPY .*services/", text, re.MULTILINE
        ), (
            f"{doc}: `docker compose run {service} {command}` — "
            f"{dockerfile.relative_to(REPO)} never copies {module.group(1)}"
        )
