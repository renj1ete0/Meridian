"""The compose topology is a security boundary (`P1-22`, spec §3, §11).

These read `docker-compose.yml` and assert the properties that make the split
worth having. They are drift tests: the file is edited by hand, the failure mode
is silent — a service keeps working perfectly while sitting on the wrong network
— and nothing else in the system would notice.

The two properties that matter both concern `crawl4ai`, because it renders
hostile pages in a real browser and is the most likely thing here to be
compromised: it must have no route to the database, and it must hold no
credentials.
"""

from __future__ import annotations

import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

COMPOSE = pathlib.Path(__file__).resolve().parents[2] / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def networks_of(compose: dict, service: str) -> set[str]:
    return set(compose["services"][service].get("networks") or [])


# --------------------------------------------------------------------------
# The networks themselves


def test_the_internal_network_has_no_route_out(compose: dict) -> None:
    """`internal: true` is the whole mechanism. Without it the split is cosmetic."""
    assert compose["networks"]["internal"].get("internal") is True


def test_the_egress_network_is_not_internal(compose: dict) -> None:
    """It exists precisely to allow outbound traffic."""
    assert compose["networks"]["egress"].get("internal") is not True


# --------------------------------------------------------------------------
# crawl4ai is the reason for the split


def test_the_browser_has_no_route_to_the_database(compose: dict) -> None:
    """It renders hostile content; it must not be able to reach Postgres.

    Asserted through the networks rather than by naming postgres, so it still
    holds if the database moves or another service joins `internal`.
    """
    browser = networks_of(compose, "crawl4ai")
    postgres = networks_of(compose, "postgres")
    assert browser, "crawl4ai must declare its networks explicitly"
    assert not (browser & postgres), (
        f"crawl4ai shares {browser & postgres} with postgres; it must not"
    )


def test_the_browser_holds_no_credentials(compose: dict) -> None:
    """`env_file` would hand a browser sandbox every password in `.env`.

    This is why `x-common` carries no `env_file`: a shared default is exactly
    how the browser ends up holding database credentials, and it did.
    """
    service = compose["services"]["crawl4ai"]
    assert "env_file" not in service
    env = service.get("environment") or {}
    leaked = [k for k in env if any(s in k.upper() for s in ("PG_", "POSTGRES", "TUNNEL"))]
    assert not leaked, f"crawl4ai is given {leaked}"


def test_the_browser_publishes_no_ports(compose: dict) -> None:
    """§6.4: reachable from the worker only, never through the tunnel."""
    assert "ports" not in compose["services"]["crawl4ai"]


def test_the_browser_has_a_health_check(compose: dict) -> None:
    """`P1-26`: the worker degrades to static silently, so the browser has to
    be able to say whether it is actually up."""
    assert compose["services"]["crawl4ai"].get("healthcheck")


def test_hooks_are_disabled_on_the_browser(compose: dict) -> None:
    """Crawl4AI's hooks are remote code execution by design."""
    env = compose["services"]["crawl4ai"].get("environment") or {}
    assert str(env.get("CRAWL4AI_HOOKS_ENABLED", "")).lower() == "false"


# --------------------------------------------------------------------------
# Everything else sits where it should


def test_postgres_cannot_reach_the_internet(compose: dict) -> None:
    assert networks_of(compose, "postgres") == {"internal"}


def test_the_worker_is_on_both(compose: dict) -> None:
    """It is the only service that both fetches the open web and writes to the
    database, which is exactly why it needs both and nothing else does."""
    assert networks_of(compose, "worker") >= {"internal", "egress"}


def test_only_named_services_write_from_egress(compose: dict) -> None:
    """A service on `egress` that also reaches Postgres is a route from hostile
    content to the database, so the set of them is an allowlist rather than a
    property — each entry is a decision somebody made and can be argued with.

    `scheduler` is the newest and the one worth justifying (`B-15`). It needs
    `egress` for exactly one of the five jobs it spawns — `worker.digest`
    reaches Telegram — and the other four want nothing outside `internal`. It
    is on both anyway, because the alternative is a scheduled job that fails to
    send and says so only in `last_error`, which is the failure mode this task
    existed to remove.

    What it costs is honest: the jobs inherit the container's environment, so
    `worker.harvest` parses text the crawler fetched inside a container that
    can reach the internet. That is the same trade `worker` already makes, in
    the same image, which is why it is tolerable here and would not be for a
    service that had no such reason.
    """
    both = {
        name for name in compose["services"] if {"internal", "egress"} <= networks_of(compose, name)
    }
    assert both <= {"worker", "scheduler", "orchestrator", "cloudflared"}, f"unexpected: {both}"


def test_no_service_sets_both_network_mode_and_networks(compose: dict) -> None:
    """Compose refuses a file that sets both, so this is a startup failure
    rather than a subtle one — and it shipped that way."""
    for name, service in compose["services"].items():
        assert not ("network_mode" in service and "networks" in service), name


def test_every_service_declares_its_networks(compose: dict) -> None:
    """`x-common` no longer supplies a default, so an omission means the
    service silently lands on compose's default network instead.

    `network_mode: none` counts, and is the only honest way to say "no
    network" — `B-16`'s `chown` one-shot needs none, and saying nothing gave
    the one container here that runs as root a route to the internet.
    """
    for name, service in compose["services"].items():
        declared = service.get("networks") or service.get("network_mode") == "none"
        assert declared, f"{name} declares no networks"


# --------------------------------------------------------------------------
# Egress restriction (task P1-25)
# --------------------------------------------------------------------------


def test_every_network_pins_its_subnet(compose: dict) -> None:
    """Host firewall rules need a target that does not move.

    Docker allocates bridge subnets from a pool, and the allocation changes when
    networks are recreated. A rule written against the old one does not error —
    it matches nothing, protects nothing, and is indistinguishable from a rule
    that works. That is the whole failure mode `P1-25` exists to close, so the
    subnets being pinned is not a detail of this compose file, it is the thing
    the defence rests on.
    """
    for name, network in compose["networks"].items():
        config = (network.get("ipam") or {}).get("config") or []
        assert config and config[0].get("subnet"), f"network {name} does not pin a subnet"


def test_the_pinned_subnets_are_inside_the_range_the_rules_cover(compose: dict) -> None:
    """Drift between the compose file and `deploy/egress-restrict.nft`.

    The rules allow Meridian's own networks by a single supernet and drop the
    rest of RFC1918. A network moved outside that supernet would be dropped by
    its own firewall, which fails loudly — but a network moved outside it and
    then *excluded* from the drop would be silently unprotected, and this is the
    cheap check that keeps the two files describing the same topology.
    """
    import ipaddress
    from pathlib import Path

    rules = Path(__file__).resolve().parents[2] / "deploy/egress-restrict.nft"
    text = rules.read_text()

    supernet = ipaddress.ip_network("172.31.240.0/22")
    assert str(supernet) in text, "the nft rules no longer name the supernet this test checks"

    for name, network in compose["networks"].items():
        subnet = ipaddress.ip_network(network["ipam"]["config"][0]["subnet"])
        assert subnet.subnet_of(supernet), (
            f"network {name} ({subnet}) is outside {supernet}, which the firewall rules "
            "treat as Meridian's own — it would be blocked, or worse, exempted"
        )


def test_the_worker_has_a_health_check(compose: dict) -> None:
    """`P5-08`. `restart: unless-stopped` covers a worker that exits and does
    nothing for one still running and no longer working — a wedged fetch, a
    pool that never recovers. Without a healthcheck, Docker cannot tell that
    from a worker that happens to be busy, and the restart that would fix it
    never fires."""
    assert compose["services"]["worker"].get("healthcheck")


def test_every_long_running_service_can_be_probed(compose: dict) -> None:
    """A service with no healthcheck is one the supervisor can only restart when
    it crashes. Listed explicitly rather than "all services", because `api` is
    probed through its own HTTP `/health` and the one-shot passes are not
    long-running at all."""
    for name in ("postgres", "crawl4ai", "worker", "embedder"):
        assert compose["services"][name].get("healthcheck"), f"{name} cannot be probed"


# --------------------------------------------------------------------------
# Environment variables nothing reads (task B-12)
# --------------------------------------------------------------------------
#
# `docker-compose.yml` set `MERIDIAN_EMBEDDER_CACHE` and the code reads
# `MERIDIAN_EMBED_CACHE`. Nothing failed: the embedder simply fell back to the
# library's default cache, inside the container, on a layer nobody mounted — so
# the `/models` volume was never used and 2.3 GB of weights downloaded again on
# every recreate. A misspelt variable is silent by construction, because the
# whole point of `os.environ.get(name, default)` is not to raise.
#
# Derived from the files rather than listing known names, so a variable added
# next year is covered without anyone remembering this test exists.

REPO = COMPOSE.parent
COMPOSE_FILES = sorted(REPO.glob("docker-compose*.yml"))

#: Set in compose, read by something that is not Python. Each needs a reason,
#: because the empty case is the one worth defending: an entry here is a
#: variable nothing in this repository can be shown to use.
NOT_READ_BY_PYTHON = {
    # Read by the Postgres entrypoint and by scripts/init-roles.sh.
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "PG_USER",
    "PG_PASSWORD",
    "PG_RW_PASSWORD",
    "PG_RO_PASSWORD",
    # Read by `huggingface_hub` inside our own image rather than by our code:
    # it turns off the network probes a container with no route out cannot
    # answer (`B-20`).
    "HF_HUB_OFFLINE",
    # Read by the upstream images' own entrypoints.
    "SEARXNG_BASE_URL",
    "CRAWL4AI_API_TOKEN",
    "CRAWL4AI_HOOKS_ENABLED",
    "TUNNEL_TOKEN",
}


def _env_names() -> set[str]:
    """Every variable name set in any compose file's `environment:` block."""
    names: set[str] = set()
    for path in COMPOSE_FILES:
        doc = yaml.safe_load(path.read_text())
        for service in (doc.get("services") or {}).values():
            if not isinstance(service, dict):
                continue
            env = service.get("environment")
            if isinstance(env, dict):
                names |= set(env)
            elif isinstance(env, list):
                names |= {str(item).split("=", 1)[0] for item in env}
    return names


def _python_source() -> str:
    roots = ("packages", "services", "scripts", "migrations")
    return "\n".join(
        p.read_text(errors="ignore") for root in roots for p in (REPO / root).rglob("*.py")
    )


def test_the_compose_files_declare_some_environment() -> None:
    """Guard on the parse. A walk that found nothing would make the assertion
    below vacuously true."""
    names = _env_names()

    assert len(names) > 10, names
    assert "PG_RW_URL" in names


def test_every_variable_compose_sets_is_read_by_something() -> None:
    """A misspelt environment variable never raises — it silently takes a
    default, which is how `MERIDIAN_EMBEDDER_CACHE` sat in production unread
    while the model volume it was supposed to point at went unused."""
    source = _python_source()
    unread = {
        name
        for name in _env_names()
        if name not in NOT_READ_BY_PYTHON
        and not re.search(rf"""["']{re.escape(name)}["']""", source)
    }

    assert not unread, (
        f"compose sets these and no Python reads them — check the spelling: {sorted(unread)}"
    )


def test_the_exemptions_are_not_a_dumping_ground() -> None:
    """The other direction. An exemption that outlives its variable turns this
    list into documentation of a problem that no longer exists, and the next
    real typo hides among the stale entries."""
    declared = _env_names()
    stale = {name for name in NOT_READ_BY_PYTHON if name not in declared}

    assert not stale, f"exempted but no longer set by any compose file: {sorted(stale)}"


# --------------------------------------------------------------------------
# A service that is running is not the same as a service anything talks to
# --------------------------------------------------------------------------
#
# `docker-compose.local.yml` ran the embedding sidecar, and did not tell the API
# where it was. Nothing failed. `RemoteEmbedder.from_env()` returns None when
# `MERIDIAN_EMBEDDER_URL` is unset — absent is a supported state, deliberately
# (`P2-07`) — so every search ran the lexical arm alone and reported
# `degraded_reason: "This API has no embedder"` next to a healthy sidecar with
# the model in it.
#
# That sentence is true of a deployment with no embedder and false here, and
# there is no way to tell the two apart from the outside. Hence a test: if a
# compose file goes to the trouble of running the sidecar, the services that
# would use it have to be able to find it.

#: How to recognise the sidecar without naming a service: it is the one started
#: with the embed server's module, whatever it is called.
EMBEDSERVER = "worker.embedserver"

#: The services that call `RemoteEmbedder.from_env()` — the API for the vector
#: arm of a search, the worker for `P2-19`'s backfill.
EMBEDDER_CLIENTS = ("api", "worker")


def _command_of(service: dict) -> str:
    command = service.get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command or "")


def _has_variable(service: dict, name: str) -> bool:
    """Whether a service is given a variable, by either route.

    `env_file` counts. Production's API takes its whole environment from `.env`
    rather than listing it, so a test that only read `environment:` would call
    the deployed stack broken and be wrong.
    """
    if service.get("env_file"):
        return True
    env = service.get("environment")
    if isinstance(env, dict):
        return name in env
    if isinstance(env, list):
        return any(str(item).split("=", 1)[0] == name for item in env)
    return False


def _compose_docs() -> list[tuple[pathlib.Path, dict]]:
    return [(path, yaml.safe_load(path.read_text())) for path in COMPOSE_FILES]


def test_there_is_a_compose_file_that_runs_the_sidecar() -> None:
    """Guard on the discovery. If the sidecar is never recognised, the test
    below passes over an empty set and asserts nothing at all."""
    running = [
        path.name
        for path, doc in _compose_docs()
        for service in (doc.get("services") or {}).values()
        if isinstance(service, dict) and EMBEDSERVER in _command_of(service)
    ]

    assert running, f"no compose file starts {EMBEDSERVER}; has it been renamed?"


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_a_stack_that_runs_the_embedder_tells_its_clients_where_it_is(
    path: pathlib.Path,
) -> None:
    """Running the model and not pointing anything at it halves retrieval
    silently, because an unset URL is indistinguishable from a deployment that
    chose to have no embedder."""
    doc = yaml.safe_load(path.read_text())
    services = doc.get("services") or {}

    if not any(
        isinstance(s, dict) and EMBEDSERVER in _command_of(s) for s in services.values()
    ):
        pytest.skip(f"{path.name} does not run the sidecar")

    blind = [
        name
        for name in EMBEDDER_CLIENTS
        if name in services and not _has_variable(services[name], "MERIDIAN_EMBEDDER_URL")
    ]

    assert not blind, (
        f"{path.name} runs {EMBEDSERVER} but {blind} cannot find it — "
        f"searches there will silently report having no embedder"
    )



# --------------------------------------------------------------------------
# Compose fails on what it cannot build, and publishes nothing it cannot reach
# --------------------------------------------------------------------------
#
# Two defects of the same shape, both found by trying to bring a stack up
# rather than by reading the file (`B-05`, `B-18`).
#
# `web` and then `orchestrator` carried `build:` pointing at a Dockerfile
# nobody had written. Compose does not skip those — it fails the whole command
# with `lstat ...: no such file or directory` — so the production stack could
# not be brought up at all, and had not been.
#
# And `api` published `127.0.0.1:21114:8000` while attached only to an
# `internal: true` network, which has no gateway for the host to forward to.
# That is not an error either. It is a port that silently does not exist,
# under a comment promising it does.


def _build_dockerfile(service: dict) -> pathlib.Path | None:
    spec = service.get("build")
    if isinstance(spec, dict):
        named = spec.get("dockerfile")
        if named:
            return REPO / str(named)
        return REPO / str(spec.get("context", ".")) / "Dockerfile"
    if isinstance(spec, str):
        return REPO / spec / "Dockerfile"
    return None


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_every_service_up_starts_can_actually_be_built(path: pathlib.Path) -> None:
    """A missing Dockerfile is not a skipped service, it is a failed `up`.

    Profile-gated services are exempt because `up` does not start them — that
    is how something can be declared before it is built, which is the whole
    reason `orchestrator` is allowed to sit here with no image.
    """
    services = yaml.safe_load(path.read_text()).get("services") or {}

    missing = {
        name: str(dockerfile.relative_to(REPO))
        for name, service in services.items()
        if isinstance(service, dict) and not service.get("profiles")
        and (dockerfile := _build_dockerfile(service)) is not None
        and not dockerfile.exists()
    }

    assert not missing, (
        f"{path.name}: `docker compose up` would fail on {missing} — compose "
        f"does not skip a service whose Dockerfile is absent. Profile-gate it "
        f"until the image exists"
    )


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_nothing_publishes_a_port_it_cannot_publish(path: pathlib.Path) -> None:
    """`ports:` on a service attached only to `internal: true` networks does
    nothing at all, and says the opposite."""
    doc = yaml.safe_load(path.read_text())
    services = doc.get("services") or {}
    networks = doc.get("networks") or {}

    inert = {}
    for name, service in services.items():
        if not isinstance(service, dict) or not service.get("ports"):
            continue
        attached = service.get("networks") or []
        # No `networks:` at all means the default bridge, which is routable.
        if attached and not any(
            not (networks.get(n) or {}).get("internal") for n in attached
        ):
            inert[name] = service["ports"]

    assert not inert, (
        f"{path.name}: {inert} — every network these are on is `internal: "
        f"true`, so Docker installs no gateway and the published port is inert"
    )


# --------------------------------------------------------------------------
# A service running a different command inherits the image's healthcheck
# --------------------------------------------------------------------------
#
# Omitting `healthcheck:` does not give a container none — it gets the image's.
# `B-15` added `scheduler` as the worker image under `python -m
# worker.scheduler`, said nothing about health, and inherited a probe that
# imports `worker.main` and checks poppler. That passes for as long as the
# package tree is intact, so a wedged scheduler would have reported `healthy`
# for ever *and* suppressed the restart that no probe would have left to
# `restart: unless-stopped`.
#
# So: a long-running service that overrides the image's command has to say what
# its health is — a real probe, or `disable: true` to mean "unprobed, honestly".
# One-shots are exempt because they exit, and a healthcheck on something that
# runs once and stops is a question with no answer.


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_a_service_running_its_own_command_says_what_its_health_is(
    path: pathlib.Path,
) -> None:
    doc = yaml.safe_load(path.read_text())
    services = doc.get("services") or {}

    silent = [
        name
        for name, service in services.items()
        if isinstance(service, dict)
        and service.get("command")
        and service.get("build")
        and not service.get("profiles")          # one-shots exit; exempt
        and "healthcheck" not in service
    ]

    assert not silent, (
        f"{path.name}: {silent} override the image's command and inherit its "
        f"healthcheck, which is probing a process they do not run. Declare one "
        f"or set `healthcheck: {{disable: true}}`"
    )
