"""Pull-based deployment, auto-update and the split embedder (task P3-12).

What these hold is policy, not syntax: Watchtower only ever follows images that
pass through promotion; state and third-party images are never auto-updated;
nothing auto-updates on a plain `up`; on the split layout the worker — which
fetches the open web — never joins the network with a LAN route; and the
embedder node will not start with its port open and no token.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
LABEL = "com.centurylinklabs.watchtower.enable"
#: Never auto-updated: the database, the browser, and images we do not build.
NEVER = {"postgres", "crawl4ai", "searxng", "cloudflared", "watchtower"}


class _Loader(yaml.SafeLoader):
    """Compose's `!override` / `!reset` tags, read as their plain value."""


for tag in ("!override", "!reset"):
    _Loader.add_constructor(
        tag,
        lambda loader, node: (
            loader.construct_mapping(node)
            if isinstance(node, yaml.MappingNode)
            else loader.construct_sequence(node)
        ),
    )


def load(path: str) -> dict:
    return yaml.load((ROOT / path).read_text(), Loader=_Loader)


@pytest.fixture(scope="module")
def main() -> dict:
    return load("docker-compose.yml")["services"]


def labelled(services: dict) -> set[str]:
    return {
        name for name, svc in services.items() if (svc.get("labels") or {}).get(LABEL) == "true"
    }


def test_watchtower_only_follows_images_that_are_promoted(main) -> None:
    """A labelled service whose image is not the registry's channel tag would
    update from somewhere a promotion never touched."""
    for name in labelled(main):
        image = main[name]["image"]
        assert image.startswith("${MERIDIAN_REGISTRY:-"), name
        assert image.endswith(":${MERIDIAN_TAG:-stable}"), f"{name} follows {image}"


def test_state_and_third_party_images_are_never_auto_updated(main) -> None:
    assert not (labelled(main) & NEVER)
    # And the application services are all covered: one left out runs old code
    # beside new code after every promotion.
    assert {"worker", "embed", "scheduler", "api", "web", "embedder", "migrate"} <= labelled(main)


def test_nothing_auto_updates_on_a_plain_up(main) -> None:
    for name in ("watchtower", "migrate"):
        assert main[name].get("profiles") == ["autoupdate"], name


def test_watchtower_touches_only_what_opted_in(main) -> None:
    env = main["watchtower"]["environment"]
    assert env["WATCHTOWER_LABEL_ENABLE"] == "true"
    assert "internal" not in (main["watchtower"].get("networks") or []), "it needs no database"


def test_a_single_machine_still_uses_its_own_embedder(main) -> None:
    urls = {
        svc["environment"]["MERIDIAN_EMBEDDER_URL"]
        for svc in main.values()
        if isinstance(svc.get("environment"), dict)
        and "MERIDIAN_EMBEDDER_URL" in svc["environment"]
    }
    assert urls == {"${MERIDIAN_EMBEDDER_URL:-http://embedder:8100}"}


def test_the_split_layout_parks_the_embedder_and_keeps_the_worker_off_the_lan(main) -> None:
    split = load("deploy/split/remote-embedder.yml")["services"]
    assert split["embedder"]["profiles"] == ["local-embedder"], "a profile nothing starts"
    merged = {name: {**main[name], **split.get(name, {})} for name in main}
    for name, svc in merged.items():
        assert "embedder" not in (svc.get("depends_on") or {}), (
            f"{name} waits for a parked embedder"
        )
    callers = {"api", "embed", "scheduler", "orchestrator"}
    for name in callers:
        assert "lan" in merged[name]["networks"], f"{name} cannot reach the embedding node"
    assert "lan" not in merged["worker"]["networks"], "the worker fetches the open web"


def test_the_embedding_node_refuses_to_start_without_its_token() -> None:
    node = load("deploy/embedder-node/docker-compose.yml")["services"]
    token = node["embedder"]["environment"]["MERIDIAN_EMBEDDER_TOKEN"]
    assert token.startswith("${MERIDIAN_EMBEDDER_TOKEN:?"), (
        "a published port with an optional token"
    )
    assert node["embedder"]["labels"][LABEL] == "true"
    assert node["embedder"]["image"].endswith(":${MERIDIAN_TAG:-stable}")


def test_the_lan_exception_is_narrow_and_ahead_of_the_drop() -> None:
    rules = (ROOT / "deploy/egress-restrict.nft").read_text()
    allow = rules.index("@lan_allow accept")
    drop = rules.index("} drop")
    assert allow < drop, "an accept after the drop never runs"
    line = rules[rules.rfind("\n", 0, allow) : allow]
    assert "ip saddr 172.31.242.0/24" in line, "the exception must be for the `lan` subnet only"
    lan = load("docker-compose.yml")["networks"]["lan"]["ipam"]["config"][0]["subnet"]
    assert lan == "172.31.242.0/24"
    assert re.search(r"set lan_allow \{\s*type ipv4_addr \. inet_service\s*\}", rules), (
        "not empty by default"
    )


def test_promotion_names_every_image_the_build_pushes() -> None:
    built = set(
        re.findall(r'^\s*"([a-z0-9]+)\|', (ROOT / "scripts/build_and_push.sh").read_text(), re.M)
    )
    promoted = re.search(r"^ALL=\(([^)]*)\)", (ROOT / "scripts/promote.sh").read_text(), re.M)
    assert promoted and set(promoted.group(1).split()) == built


def test_on_the_split_layout_the_stack_never_loads_the_model_itself() -> None:
    """The fallback that loads the model in-process is right on one machine and
    wrong on a board shared with Postgres and the browser."""
    split = load("deploy/split/remote-embedder.yml")["services"]
    assert split["embed"]["environment"]["MERIDIAN_EMBED_REMOTE_ONLY"] == "1"
