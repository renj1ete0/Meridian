"""The podman deployment (task `B-226`, ADR 0021).

Compose files shared with Docker, plus an overlay, systemd units and a Butane config
for Fedora CoreOS, and an overlay for a GPU node on Windows. What is checked is that
these pieces agree with each other and with the compose files they adjust, since each
was written by hand and nothing else executes them before the server does.
"""

from __future__ import annotations

import configparser
import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
PODMAN = ROOT / "deploy/podman"
NODE = ROOT / "deploy/embedder-node"
GUIDE = ROOT / "docs/guides/deploy-podman.md"


class _Loader(yaml.SafeLoader):
    """Compose's `!override` / `!reset` tags, kept as a marker so a test can see them."""


class Tagged(dict):
    def __init__(self, tag: str, value) -> None:
        super().__init__(tag=tag, value=value)


for _tag in ("!override", "!reset"):
    _Loader.add_constructor(
        _tag,
        lambda loader, node, tag=_tag: Tagged(
            tag,
            loader.construct_mapping(node)
            if isinstance(node, yaml.MappingNode)
            else loader.construct_sequence(node)
            if isinstance(node, yaml.SequenceNode)
            else loader.construct_scalar(node),
        ),
    )


def load(path: pathlib.Path) -> dict:
    return yaml.load(path.read_text(), Loader=_Loader)


def plain(value):
    return value["value"] if isinstance(value, Tagged) else value


def unit(path: pathlib.Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string(path.read_text())
    return parser


@pytest.fixture(scope="module")
def butane() -> dict:
    return yaml.safe_load((PODMAN / "meridian.bu").read_text())


# --------------------------------------------------------------------------
# The Butane config installs what is in the folder, and only what exists
# --------------------------------------------------------------------------


def test_every_unit_in_the_folder_is_installed(butane) -> None:
    """A unit written and never installed is a step the server silently skips."""
    installed = {
        pathlib.PurePath(f["path"]).name
        for f in butane["storage"]["files"]
        if f["path"].startswith("/etc/systemd/system/")
    }
    written = {p.name for p in PODMAN.iterdir() if p.suffix in {".service", ".timer"}}
    assert written <= installed, f"not installed: {sorted(written - installed)}"
    assert "meridian-backup.timer" in installed, "the shared backup timer is not installed"


def test_every_local_file_it_names_exists(butane) -> None:
    for entry in butane["storage"]["files"]:
        local = (entry.get("contents") or {}).get("local")
        if local:
            assert (ROOT / "deploy" / local).is_file(), f"{entry['path']} <- {local}"


def test_every_enabled_unit_is_one_it_installs(butane) -> None:
    installed = {pathlib.PurePath(f["path"]).name for f in butane["storage"]["files"]}
    for entry in butane["systemd"]["units"]:
        if entry["name"].startswith("meridian"):
            assert entry["name"] in installed, entry["name"]


def test_the_compose_binary_is_pinned_and_verified(butane) -> None:
    """A floating download would put whatever is newest on the server, unchecked."""
    binary = next(f for f in butane["storage"]["files"] if f["path"].endswith("docker-compose"))
    source = binary["contents"]["source"]
    assert re.search(r"/download/v\d+\.\d+\.\d+/", source), source
    assert re.fullmatch(r"sha256-[0-9a-f]{64}", binary["contents"]["verification"]["hash"])
    version = re.search(r"/download/(v[\d.]+)/", source).group(1)
    assert version.lstrip("v") in GUIDE.read_text(), "the guide names a different version"


def test_os_updates_reboot_only_in_a_window(butane) -> None:
    """Unconfigured, Zincati reboots into an update whenever one is staged."""
    import tomllib

    entry = next(f for f in butane["storage"]["files"] if f["path"].startswith("/etc/zincati/"))
    config = tomllib.loads((ROOT / "deploy" / entry["contents"]["local"]).read_text())
    assert config["updates"]["strategy"] == "periodic"
    assert config["updates"]["periodic"]["window"], "a periodic strategy with no window"


def test_the_ssh_key_is_a_placeholder_rather_than_someone_elses(butane) -> None:
    keys = butane["passwd"]["users"][0]["ssh_authorized_keys"]
    assert all("REPLACE" in key for key in keys)


# --------------------------------------------------------------------------
# The units agree with the Docker ones and with the binary they run
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(PODMAN.glob("*.service")), ids=lambda p: p.name)
def test_compose_runs_against_podmans_socket(path) -> None:
    text = path.read_text()
    if "docker-compose" not in text and "backup.sh" not in text:
        return
    # By text: a unit may repeat `Environment=`, and configparser keeps only the last.
    assert "Environment=DOCKER_HOST=unix:///run/podman/podman.sock" in text
    assert "/usr/bin/docker " not in text, "there is no docker CLI on CoreOS"
    assert "Requires=podman.socket" in text


def test_the_podman_stack_unit_waits_as_long_as_the_docker_one() -> None:
    """The worker drains its fetches on SIGTERM; a shorter stop throws them away."""
    docker = unit(ROOT / "deploy/meridian.service")["Service"]["TimeoutStopSec"]
    podman = unit(PODMAN / "meridian.service")["Service"]["TimeoutStopSec"]
    assert podman == docker


def test_the_label_unit_runs_once_and_before_the_stack() -> None:
    parsed = unit(PODMAN / "meridian-label.service")["Unit"]
    assert parsed["ConditionPathExists"].startswith("!")
    assert "meridian.service" in parsed["Before"]


def test_the_update_does_not_skip_images_that_also_build() -> None:
    """Every app service has a `build:` section, so `--ignore-buildable` pulls nothing."""
    text = (PODMAN / "meridian-update.service").read_text()
    assert "--ignore-buildable" not in text.replace("`--ignore-buildable`", "")
    assert "pull" in text and "up -d" in text


def test_the_egress_unit_loads_the_file_ignition_writes(butane) -> None:
    written = next(f["path"] for f in butane["storage"]["files"] if f["path"].endswith(".nft"))
    assert written in (PODMAN / "meridian-egress.service").read_text()


# --------------------------------------------------------------------------
# The overlays
# --------------------------------------------------------------------------


def test_the_stack_overlay_parks_watchtower_by_replacing_its_profiles() -> None:
    """Merged, the list kept `autoupdate` and Watchtower still started (seen on podman)."""
    profiles = load(PODMAN / "podman.yml")["services"]["watchtower"]["profiles"]
    assert isinstance(profiles, Tagged) and profiles["tag"] == "!override"
    assert "autoupdate" not in profiles["value"]


def test_the_overlay_only_names_services_the_stack_has() -> None:
    base = load(ROOT / "docker-compose.yml")["services"]
    assert set(load(PODMAN / "podman.yml")["services"]) <= set(base)


def test_the_guide_names_files_that_exist() -> None:
    text = GUIDE.read_text()
    for line in re.findall(r"COMPOSE_FILE=([^\s`]+)", text):
        sep = ";" if ";" in line else ":"
        base = NODE if "gpu.yml" in line else ROOT
        for name in line.split(sep):
            assert (base / name).is_file(), f"{name} in {line}"


@pytest.fixture(scope="module")
def node() -> dict[str, dict]:
    return {
        name: load(NODE / name)["services"]
        for name in ("docker-compose.yml", "gpu.yml", "podman-windows.yml")
    }


def test_every_service_that_mounts_the_models_dir_gets_the_named_volume(node) -> None:
    """On Windows the host path is inside the podman machine, where it does not exist."""
    mounting = {
        name
        for file in ("docker-compose.yml", "gpu.yml")
        for name, svc in node[file].items()
        if any("MODELS_DIR" in str(v) for v in plain(svc.get("volumes")) or [])
    }
    windows = node["podman-windows.yml"]
    assert mounting, "nothing mounts MODELS_DIR; the test proves nothing"
    for name in mounting:
        volumes = windows.get(name, {}).get("volumes")
        assert isinstance(volumes, Tagged) and volumes["tag"] == "!override", name
        assert volumes["value"] == ["models:/models"], name


def test_every_gpu_service_gets_the_card_by_its_cdi_name(node) -> None:
    """gpu.yml's reservation is Docker's NVIDIA runtime; podman resolves CDI names."""
    reserving = {
        name
        for name, svc in node["gpu.yml"].items()
        if "devices" in str(plain(svc.get("deploy")) or "")
    }
    windows = node["podman-windows.yml"]
    assert reserving
    for name in reserving:
        assert windows[name]["devices"] == ["nvidia.com/gpu=all"], name
        assert "devices" not in str(plain(windows[name].get("deploy")) or ""), name
        assert isinstance(windows[name].get("deploy"), Tagged), f"{name} keeps the reservation"


def test_no_service_holds_a_capability_for_a_feature_that_was_not_built() -> None:
    """ADR 0022: no Wake-on-LAN, so the orchestrator, which holds the model keys, needs no
    raw sockets."""
    base = load(ROOT / "docker-compose.yml")["services"]
    assert all("NET_RAW" not in (svc.get("cap_add") or []) for svc in base.values())


def test_the_windows_overlay_parks_watchtower(node) -> None:
    profiles = node["podman-windows.yml"]["watchtower"]["profiles"]
    assert isinstance(profiles, Tagged) and profiles["value"] == ["docker-only"]
