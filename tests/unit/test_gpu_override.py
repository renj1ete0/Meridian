"""The GPU override (`B-130`'s companion, `deploy/gpu/gpu-embedder.yml`).

Not under the root `docker-compose*.yml` glob that `test_compose_topology` reads,
so the properties that matter for it are held here: every variable it sets is
read by something, the card goes to the sidecar and nowhere else, and the
backfill beside it never loads a second copy of the model on a CPU.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
OVERRIDE = REPO / "deploy" / "gpu" / "gpu-embedder.yml"


@pytest.fixture(scope="module")
def override() -> dict:
    return yaml.safe_load(OVERRIDE.read_text())


@pytest.fixture(scope="module")
def base() -> dict:
    return yaml.safe_load((REPO / "docker-compose.yml").read_text())


def _python_source() -> str:
    return "\n".join(
        p.read_text(errors="ignore")
        for root in ("packages", "services")
        for p in (REPO / root).rglob("*.py")
    )


def test_it_only_names_services_the_stack_has(override, base) -> None:
    """An override for a misspelt service silently adds a new, broken one."""
    assert set(override["services"]) <= set(base["services"])


def test_every_variable_it_sets_is_read_by_something(override) -> None:
    source = _python_source()
    names = {n for s in override["services"].values() for n in (s.get("environment") or {})}
    assert names
    unread = {n for n in names if f'"{n}"' not in source}
    assert not unread, f"set by the GPU override and read by nothing: {sorted(unread)}"


def _gpu_services(doc: dict) -> set[str]:
    out = set()
    for name, service in doc["services"].items():
        devices = (
            ((service.get("deploy") or {}).get("resources") or {})
            .get("reservations", {})
            .get("devices", [])
        )
        if any("gpu" in d.get("capabilities", []) for d in devices):
            out.add(name)
    return out


def test_the_card_goes_to_the_sidecar_alone(override) -> None:
    """The browser and the crawler handle hostile content; neither gets a device."""
    assert _gpu_services(override) == {"embedder"}


def test_the_sidecar_is_told_to_use_it(override) -> None:
    assert override["services"]["embedder"]["environment"]["MERIDIAN_EMBED_DEVICE"] == "cuda"


def test_the_backfill_beside_it_never_loads_the_model_itself(override) -> None:
    """Its container has no card: a fallback would embed on the CPU at a fraction of the speed."""
    env = override["services"]["embed"]["environment"]
    assert env["MERIDIAN_EMBED_REMOTE_ONLY"] in {"1", "true", "yes"}


def test_the_backfill_batch_is_a_number_the_worker_accepts(override) -> None:
    raw = override["services"]["embed"]["environment"]["MERIDIAN_EMBED_CHUNK_BATCH"]
    assert isinstance(raw, str), "compose wants environment values quoted"
    assert int(raw) > 0
