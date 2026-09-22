"""The orchestrator is a different image, and why that has to stay true
(task `P4-17`, §2.1, §6.3, §11.7).

§2.1's invariant is that ingestion never calls an LLM, and `P4-15` made it
mechanical rather than remembered: the SDK lives behind `meridian-core[agent]`
and the worker image is built without it, so the fast loop *cannot* reach a
model even if somebody imports the wrong thing.

`P4-16` then put the synthesis stages in `worker/orchestrate.py` — the same
package — which is fine while the two are built differently and a silent
disaster the moment they are not. One `--extra agent` added to the worker's
build, or one dropped from the orchestrator's, and either the invariant is
gone or every run defers with a provider error nobody can explain.

So these tests read the two Dockerfiles against each other. They are cheap,
they need no Docker, and they fail on the line that would have caused it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKER = REPO / "services" / "worker" / "Dockerfile"
ORCHESTRATOR = REPO / "services" / "orchestrator" / "Dockerfile"


def sync_lines(dockerfile: Path) -> list[str]:
    """Every `uv sync` invocation in a Dockerfile, joined across continuations."""
    text = dockerfile.read_text().replace("\\\n", " ")
    return [line.strip() for line in text.splitlines() if "uv sync" in line]


def test_the_orchestrator_has_a_dockerfile_at_all() -> None:
    """It did not for the whole of phases 0–4, which is why the compose service
    was profile-gated: compose fails the entire command on a missing
    Dockerfile rather than skipping that service."""
    assert ORCHESTRATOR.exists()


def test_the_worker_image_cannot_reach_a_model() -> None:
    """The invariant, checked where it is actually decided."""
    for line in sync_lines(WORKER):
        assert "--extra agent" not in line, (
            "the worker image is being built with the model SDK; §2.1's "
            "guarantee is that ingestion physically cannot call one"
        )


def test_the_orchestrator_image_can() -> None:
    lines = sync_lines(ORCHESTRATOR)

    assert lines, "no dependency sync found; this test is reading the wrong file"
    assert all("--extra agent" in line for line in lines), (
        "the orchestrator image must carry `meridian-core[agent]` in every "
        "sync, or the first run defers with a provider error that reads as an "
        "outage rather than a build mistake"
    )


def test_the_orchestrator_does_not_carry_the_embedding_stack() -> None:
    """It reads chunks and writes edges. `--extra embed` would put 2.3 GB of
    model weights in an image that never embeds anything."""
    for line in sync_lines(ORCHESTRATOR):
        assert "--extra embed" not in line


def test_the_two_images_build_the_same_package() -> None:
    """A second copy of the stages would be a second thing to keep in step with
    the write tools. What differs between the crawl and the synthesis run is
    the dependency set and the entry point, and that should be all that does."""
    for dockerfile in (WORKER, ORCHESTRATOR):
        assert all("--package meridian-worker" in line for line in sync_lines(dockerfile))


def test_the_orchestrator_runs_the_daemon() -> None:
    """`--daemon` is what makes it a service rather than a process that does
    one cycle and exits into a restart loop."""
    command = ORCHESTRATOR.read_text()
    match = re.search(r"^CMD \[(.+)\]$", command, flags=re.MULTILINE)

    assert match, "no CMD found"
    assert "worker.orchestrate" in match.group(1)
    assert "--daemon" in match.group(1)


def test_the_probe_checks_for_the_sdk() -> None:
    """The one thing this image exists for. A build that silently dropped the
    extra would otherwise report healthy and defer every run."""
    assert "anthropic" in ORCHESTRATOR.read_text().split("HEALTHCHECK")[1]


@pytest.mark.parametrize("path", ["docker-compose.yml", "docker-compose.local.yml"])
def test_the_service_is_no_longer_profile_gated(path: str) -> None:
    """The profile existed only because the Dockerfile did not. Leaving it
    would mean `docker compose up -d` brings up a stack with no synthesis in
    it, silently — which is the state this task exists to end."""
    services = yaml.safe_load((REPO / path).read_text())["services"]

    assert "orchestrator" in services, f"{path} has no orchestrator service"
    assert not services["orchestrator"].get("profiles"), (
        f"{path}: the orchestrator is profile-gated, so a plain `up` leaves "
        "synthesis out of the stack"
    )


@pytest.mark.parametrize("path", ["docker-compose.yml", "docker-compose.local.yml"])
def test_the_raw_store_is_mounted_read_only(path: str) -> None:
    """§11.4. A run cites chunks; it has no business rewriting the files they
    were drawn from, and the mount is where that is enforced rather than
    hoped for."""
    volumes = yaml.safe_load((REPO / path).read_text())["services"]["orchestrator"]["volumes"]
    raw = [v for v in volumes if "/data/raw" in v]

    assert raw, f"{path}: the orchestrator cannot read the raw store"
    assert all(v.endswith(":ro") for v in raw), f"{path}: {raw} is writable"
