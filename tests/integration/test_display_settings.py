"""The display zone over HTTP (task `B-145`, ADR 0009)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from api.main import create_app
from meridian_core.db import dispose_engines
from meridian_core.models import FetchPolicy
from meridian_core.policy import GLOBAL_DOMAIN
from meridian_core.timefmt import DEFAULT_ZONE, ZONE_KEY, display_zone

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture(autouse=True)
def admin_open(monkeypatch):
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "true")


@pytest.fixture
async def client():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def restore(session_for):
    """Put the global row's zone back as it was."""
    sess = await session_for("rw")
    row = await sess.scalar(select(FetchPolicy).where(FetchPolicy.domain == GLOBAL_DOMAIN))
    before = (row.settings or {}).get(ZONE_KEY)
    await sess.commit()
    yield
    row = await sess.scalar(select(FetchPolicy).where(FetchPolicy.domain == GLOBAL_DOMAIN))
    settings = dict(row.settings or {})
    if before is None:
        settings.pop(ZONE_KEY, None)
    else:
        settings[ZONE_KEY] = before
    row.settings = settings
    await sess.commit()


async def test_a_reader_can_see_the_zone(client, restore) -> None:
    response = await client.get("/api/explore/settings")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"display_timezone", "label"}
    assert body["label"].startswith("GMT")


async def test_admin_changes_it_and_the_server_reads_it_back(client, restore, session_for) -> None:
    response = await client.put(
        "/api/admin/settings/display-timezone", json={"display_timezone": "Europe/London"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["display_timezone"] == "Europe/London"

    assert (await client.get("/api/explore/settings")).json()["display_timezone"] == "Europe/London"
    assert await display_zone(await session_for("ro")) == "Europe/London"


@pytest.mark.parametrize("bad", ["Mars/Olympus_Mons", "GMT+8", ""])
async def test_a_zone_that_does_not_exist_is_refused_and_nothing_changes(
    client, restore, bad
) -> None:
    before = (await client.get("/api/explore/settings")).json()["display_timezone"]
    response = await client.put(
        "/api/admin/settings/display-timezone", json={"display_timezone": bad}
    )
    assert response.status_code == 422
    assert (await client.get("/api/explore/settings")).json()["display_timezone"] == before


async def test_changing_the_zone_keeps_the_other_global_settings(
    client, restore, session_for
) -> None:
    sess = await session_for("ro")
    row = await sess.scalar(select(FetchPolicy).where(FetchPolicy.domain == GLOBAL_DOMAIN))
    others = {k: v for k, v in (row.settings or {}).items() if k != ZONE_KEY}
    await sess.rollback()

    await client.put("/api/admin/settings/display-timezone", json={"display_timezone": "UTC"})

    row = await sess.scalar(select(FetchPolicy).where(FetchPolicy.domain == GLOBAL_DOMAIN))
    assert {k: v for k, v in row.settings.items() if k != ZONE_KEY} == others


async def test_the_explore_route_cannot_write(client, restore) -> None:
    """The prefix is the role boundary: a PUT under /api/explore does not exist."""
    response = await client.put("/api/explore/settings", json={"display_timezone": DEFAULT_ZONE})
    assert response.status_code == 405
