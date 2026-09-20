"""The data directories are writable by the uid the services run as (`B-16`).

Docker creates a missing bind-mount source on the host as **root**. Every
application image runs as `meridian`, uid 1001, unprivileged, with `cap_drop:
ALL` and a read-only root filesystem. Those two facts together meant that on a
fresh machine the crawl fetched a 400 KB government page, failed to create
`/data/raw/<domain>/`, logged a traceback, and settled the task as
`"outcome": "success", "stored": null` — because the fetch *had* succeeded. It
kept running, and kept nothing.

Nothing in the suite could have caught it: the tests write to `tmp_path` as the
user running them, and a container's view of a bind mount does not exist until
there is a container. So what is asserted here is the agreement between the
three places the number 1001 appears — the Dockerfiles that create the user, and
the compose services that hand the directories over to it.

A drift test, in the shape §3 of AGENTS.md asks for: both sides derived from the
files, so a uid changed in one place fails rather than silently halving the
system.
"""

from __future__ import annotations

import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

REPO = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_FILES = sorted(REPO.glob("docker-compose*.yml"))
DOCKERFILES = sorted(REPO.glob("services/*/Dockerfile"))

#: The one-shot that does the `chown`, recognised by its command rather than by
#: name, so renaming the service does not quietly disconnect this.
CHOWN = "chown"

#: Services that run as their own user and own their own data directory, so
#: the uid the application images use is not the one their files should have.
#:
#: `postgres` is the only one, and it became relevant when `P4-01` gave it a
#: `build:` — the rule above keys off that, and until then it had none. The
#: database's entrypoint chowns `PGDATA` to the `postgres` user at init;
#: handing it to uid 1001 would stop the server starting.
MANAGES_ITS_OWN_DATA = frozenset({"postgres"})


def _services(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text()).get("services") or {}


def _command_of(service: dict) -> str:
    command = service.get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command or "")


def _mounts(service: dict) -> dict[str, str]:
    """Container path → host path, for short-form volumes."""
    found: dict[str, str] = {}
    for volume in service.get("volumes") or []:
        if not isinstance(volume, str):
            continue
        fields = volume.split(":")
        if len(fields) >= 2 and fields[0].startswith(("/", ".", "$")):
            found[fields[1]] = fields[0]
    return found


def image_uids() -> dict[str, int]:
    """The uid each application image creates for itself."""
    found: dict[str, int] = {}
    for path in DOCKERFILES:
        match = re.search(r"useradd[^\n]*--uid (\d+)", path.read_text())
        if match:
            found[path.parent.name] = int(match.group(1))
    return found


def chown_services(path: pathlib.Path) -> dict[str, dict]:
    return {
        name: service
        for name, service in _services(path).items()
        if isinstance(service, dict) and CHOWN in _command_of(service)
    }


def test_the_images_agree_on_one_uid() -> None:
    """Guard on the parse, and a real constraint: the API and the worker share
    the raw store, one writing and one reading, so two uids would mean one of
    them locked out of files the other made."""
    uids = image_uids()

    assert len(uids) >= 2, f"expected several images to create a user: {uids}"
    assert len(set(uids.values())) == 1, f"the images disagree on their uid: {uids}"


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_a_stack_with_bind_mounted_data_hands_it_to_that_uid(
    path: pathlib.Path,
) -> None:
    """Every host directory an unprivileged service writes to has to be chowned
    by something, or the first write fails and the crawl stores nothing."""
    services = _services(path)
    writable: set[str] = set()
    for name, service in services.items():
        if not isinstance(service, dict) or CHOWN in _command_of(service):
            continue
        if service.get("user") == "root" or not service.get("build"):
            continue
        if name in MANAGES_ITS_OWN_DATA:
            continue
        for container_path, host_path in _mounts(service).items():
            # `:ro` mounts are read-only by definition, and a root-owned
            # directory is world-readable. Only the writers matter.
            if not host_path.startswith("$"):
                continue
            if any(v.startswith(f"{host_path}:{container_path}:ro") for v in service["volumes"]):
                continue
            writable.add(container_path)

    if not writable:
        pytest.skip(f"{path.name} bind-mounts no writable data directories")

    chowned: set[str] = set()
    for service in chown_services(path).values():
        chowned |= set(_mounts(service))

    assert writable <= chowned, (
        f"{path.name}: nothing chowns {sorted(writable - chowned)} — Docker "
        f"creates them as root and the services that write to them do not run "
        f"as root"
    )


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_the_chown_names_the_uid_the_images_create(path: pathlib.Path) -> None:
    """The number 1001 lives in the Dockerfiles and is repeated in compose.
    Two copies of a number drift; this is the thread between them."""
    chowners = chown_services(path)
    if not chowners:
        pytest.skip(f"{path.name} has no chown step")

    expected = set(image_uids().values())
    for name, service in chowners.items():
        uids = {int(u) for u in re.findall(r"\b(\d{3,5}):\d{3,5}\b", _command_of(service))}
        assert uids, f"{path.name}: {name} runs a chown with no uid in it"
        assert uids <= expected, (
            f"{path.name}: {name} chowns to {sorted(uids)}, but the images "
            f"create {sorted(expected)}"
        )


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_the_chown_is_the_only_thing_running_as_root(path: pathlib.Path) -> None:
    """It needs root and nothing else does. A second `user: root` would be
    worth noticing, since the whole point of the unprivileged images is that a
    compromise in the thing fetching hostile pages is contained."""
    rooted = {
        name
        for name, service in _services(path).items()
        if isinstance(service, dict) and service.get("user") == "root"
    }

    assert rooted <= set(chown_services(path)), (
        f"{path.name}: {sorted(rooted - set(chown_services(path)))} runs as root"
    )
