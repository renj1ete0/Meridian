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


def test_the_worker_is_the_only_writer_on_egress(compose: dict) -> None:
    """A service on `egress` that also reaches Postgres is a route from hostile
    content to the database. Worker and orchestrator are the deliberate ones."""
    both = {
        name
        for name in compose["services"]
        if {"internal", "egress"} <= networks_of(compose, name)
    }
    assert both <= {"worker", "orchestrator", "cloudflared"}, f"unexpected: {both}"


def test_no_service_sets_both_network_mode_and_networks(compose: dict) -> None:
    """Compose refuses a file that sets both, so this is a startup failure
    rather than a subtle one — and it shipped that way."""
    for name, service in compose["services"].items():
        assert not ("network_mode" in service and "networks" in service), name


def test_every_service_declares_its_networks(compose: dict) -> None:
    """`x-common` no longer supplies a default, so an omission means the
    service silently lands on compose's default network instead."""
    for name, service in compose["services"].items():
        assert service.get("networks"), f"{name} declares no networks"
