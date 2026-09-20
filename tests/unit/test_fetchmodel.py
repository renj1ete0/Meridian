"""The weights fetch (task `B-14`).

The bug was not in any code: `embedder` sits on `internal`, which has no route
out, and the weights it lazily waits for are not in the image. So the sidecar
answered `loaded: false` for ever and every embed request timed out — found by
running the stack, not by building it, because nothing downloads anything at
build time and `/health` is honest about a model that simply is not loaded yet.

`worker.fetchmodel` is the container that *does* have egress, run once. These
tests are about the two properties that make it worth having — that it actually
exercises the model rather than only downloading files, and that a model of the
wrong width is refused here rather than at the first batch of a backfill — plus
the compose wiring, which is where the original mistake lived and where the
next one will be.

Nothing here loads bge-m3. `FakeEmbedder` has the same shape and the properties
under test are the wrapper's, exactly as `test_embeddings.py` argues.
"""

from __future__ import annotations

import pathlib

import pytest

from meridian_core.models.source import EMBEDDING_DIM
from worker.embeddings import EmbedderSettings, EmbeddingError, FakeEmbedder
from worker.fetchmodel import fetch

yaml = pytest.importorskip("yaml")

REPO = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_FILES = sorted(REPO.glob("docker-compose*.yml"))

FETCHMODEL = "worker.fetchmodel"
EMBEDSERVER = "worker.embedserver"


class RecordingEmbedder(FakeEmbedder):
    """A `FakeEmbedder` that remembers it was asked to encode something.

    The point of the fetch step is that it *uses* the model: a download can
    leave a cache that is missing a file, and the way that surfaces otherwise is
    four hours into a backfill.
    """

    def __init__(self, dimensions: int = EMBEDDING_DIM) -> None:
        super().__init__(dimensions)
        self.calls: list[list[str]] = []

    def embed(self, texts):  # type: ignore[no-untyped-def]
        self.calls.append(list(texts))
        return super().embed(texts)


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    """Swap the real embedder for one that costs nothing.

    `fetch` imports `BGEEmbedder` inside the function, deliberately, so the
    patch has to land on the module it is imported *from*.
    """

    def install(embedder: FakeEmbedder) -> None:
        monkeypatch.setattr(
            "worker.embeddings.BGEEmbedder", lambda settings=None: embedder, raising=True
        )

    return install


def test_it_encodes_rather_than_only_downloading(patched) -> None:
    """A cache with a file missing downloads "successfully" and fails at the
    first real batch. Encoding one string here is what turns that into a
    failure of the deploy step instead."""
    embedder = RecordingEmbedder()
    patched(embedder)

    fetch(EmbedderSettings(cache_dir="/models"))

    assert embedder.calls, "the fetch never asked the model to encode anything"


def test_it_reports_the_dimension_it_verified(patched) -> None:
    embedder = RecordingEmbedder()
    patched(embedder)

    assert fetch(EmbedderSettings(cache_dir="/models")) == EMBEDDING_DIM


def test_a_model_of_the_wrong_width_is_refused(patched) -> None:
    """`chunks.embedding` is a fixed-width vector column. A model that returns
    something else cannot be stored at all, and the cheapest moment to say so is
    the step whose whole job is to check the weights work."""
    patched(RecordingEmbedder(dimensions=EMBEDDING_DIM // 2))

    with pytest.raises(EmbeddingError) as raised:
        fetch(EmbedderSettings(cache_dir="/models", dimensions=EMBEDDING_DIM))

    assert str(EMBEDDING_DIM // 2) in str(raised.value)
    assert str(EMBEDDING_DIM) in str(raised.value)


# --------------------------------------------------------------------------
# The compose wiring, which is where the bug actually was


def _services(path: pathlib.Path) -> dict:
    return (yaml.safe_load(path.read_text()).get("services") or {})


def _command_of(service: dict) -> str:
    command = service.get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command or "")


def _running(path: pathlib.Path, module: str) -> list[str]:
    return [
        name
        for name, service in _services(path).items()
        if isinstance(service, dict) and module in _command_of(service)
    ]


#: `ro`, `rw`, `z`, `Z` and friends — the optional third field of a short-form
#: volume. Recognised so the target can be found by splitting from the right,
#: which is the only way that survives `${DATA_ROOT:-/srv/meridian}/models`:
#: the default value inside the expansion contains a colon of its own, and
#: splitting from the left hands back a fragment of the source path.
MOUNT_MODES = {"ro", "rw", "z", "Z", "cached", "delegated", "consistent"}


def _mount_targets(service: dict) -> set[str]:
    targets: set[str] = set()
    for volume in service.get("volumes") or []:
        if isinstance(volume, dict):
            if volume.get("target"):
                targets.add(str(volume["target"]))
            continue
        fields = str(volume).rsplit(":", 1)
        if len(fields) == 2 and fields[1] in MOUNT_MODES:
            fields = fields[0].rsplit(":", 1)
        if len(fields) == 2:
            targets.add(fields[1])
    return targets


def test_the_sidecar_is_recognisable_in_some_compose_file() -> None:
    """Guard on the discovery: if neither module name matches anything, every
    assertion below passes over an empty set."""
    found = {path.name: _running(path, EMBEDSERVER) for path in COMPOSE_FILES}

    assert any(found.values()), f"nothing starts {EMBEDSERVER}: {found}"


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_a_stack_that_runs_the_sidecar_can_also_fetch_its_weights(
    path: pathlib.Path,
) -> None:
    """The sidecar is on an isolated network by design and cannot download the
    model. A compose file that starts it and has no way to put the weights in
    place describes a service that can never answer."""
    if not _running(path, EMBEDSERVER):
        pytest.skip(f"{path.name} does not run the sidecar")

    assert _running(path, FETCHMODEL), (
        f"{path.name} runs the embedding sidecar but nothing fetches its "
        f"weights — it will report loaded: false for ever"
    )


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_the_fetcher_has_a_route_out_and_the_sidecar_does_not(
    path: pathlib.Path,
) -> None:
    """The whole arrangement. Egress for the one container that needs it, for
    as long as it takes to exit; none for the one that holds the model."""
    services = _services(path)
    fetchers = _running(path, FETCHMODEL)
    if not fetchers:
        pytest.skip(f"{path.name} does not fetch weights")

    for name in fetchers:
        assert "egress" in (services[name].get("networks") or []), (
            f"{path.name}: {name} downloads 2.3 GB and has no route out"
        )

    for name in _running(path, EMBEDSERVER):
        assert "egress" not in (services[name].get("networks") or []), (
            f"{path.name}: {name} holds the model and must not reach the "
            f"internet — that is what {fetchers} is for"
        )


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_the_fetcher_writes_where_the_sidecar_reads(path: pathlib.Path) -> None:
    """Two containers agreeing on a path by coincidence is the failure that
    looks exactly like success: the download runs, the sidecar still has
    nothing, and neither logs anything wrong."""
    services = _services(path)
    fetchers = _running(path, FETCHMODEL)
    if not fetchers:
        pytest.skip(f"{path.name} does not fetch weights")

    for sidecar in _running(path, EMBEDSERVER):
        wanted = services[sidecar]["environment"]["MERIDIAN_EMBED_CACHE"]
        assert wanted in _mount_targets(services[sidecar]), (
            f"{path.name}: {sidecar} reads {wanted} and mounts nothing there"
        )
        for name in fetchers:
            assert services[name]["environment"]["MERIDIAN_EMBED_CACHE"] == wanted
            assert wanted in _mount_targets(services[name]), (
                f"{path.name}: {name} writes to {wanted} and mounts nothing there"
            )


# --------------------------------------------------------------------------
# A container with no route out should be told so (task `B-20`)
# --------------------------------------------------------------------------
#
# The sidecar reaches its weights from the cache `modelfetch` filled, and it
# got there the slow way: `huggingface_hub` issues HEAD requests for the
# optional config files the cache does not hold, the isolated network answers
# `Temporary failure in name resolution`, and it retries five times with
# backoff *per file* before proceeding from cache anyway. Correct in the end,
# and two and a half minutes of it on every cold start, logged as a wall of
# warnings that look like the failure they are not.
#
# `HF_HUB_OFFLINE` says the true thing about where that container is standing.
# It is also the honest failure mode: a genuinely missing *required* file then
# raises immediately and says the cache is incomplete, rather than timing out
# against a host that was never reachable.

OFFLINE = "HF_HUB_OFFLINE"


def _environment(service: dict) -> dict[str, str]:
    env = service.get("environment")
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    if isinstance(env, list):
        pairs = [str(item).split("=", 1) for item in env]
        return {p[0]: (p[1] if len(p) > 1 else "") for p in pairs}
    return {}


def _reaches_the_internet(doc: dict, service: dict) -> bool:
    """Whether any network this service is on has a route out."""
    networks = doc.get("networks") or {}
    attached = service.get("networks") or []
    if not attached:
        # No `networks:` means compose's default bridge, which is routable.
        return service.get("network_mode") != "none"
    return any(not (networks.get(n) or {}).get("internal") for n in attached)


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_a_model_loader_with_no_route_out_is_told_so(path: pathlib.Path) -> None:
    """Every service that reads the model cache from an isolated network."""
    doc = yaml.safe_load(path.read_text())
    services = doc.get("services") or {}

    marooned = {
        name
        for name, service in services.items()
        if isinstance(service, dict)
        and _environment(service).get("MERIDIAN_EMBED_CACHE")
        and not _reaches_the_internet(doc, service)
    }
    if not marooned:
        pytest.skip(f"{path.name} has no isolated model loader")

    silent = {n for n in marooned if _environment(services[n]).get(OFFLINE) != "1"}

    assert not silent, (
        f"{path.name}: {sorted(silent)} load the model from an isolated "
        f"network and do not set {OFFLINE}=1 — every cold start will retry "
        f"against a host it cannot resolve before falling back to the cache"
    )


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_the_fetcher_is_never_put_offline(path: pathlib.Path) -> None:
    """The inverse, and the one that would actually break something: the
    container whose entire job is downloading must not be told there is no
    network."""
    services = yaml.safe_load(path.read_text()).get("services") or {}

    for name in _running(path, FETCHMODEL):
        assert _environment(services[name]).get(OFFLINE) != "1", (
            f"{path.name}: {name} downloads the weights and cannot do it offline"
        )
