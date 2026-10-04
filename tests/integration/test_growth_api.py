"""`/api/explore/growth` (task `B-140`, ADR 0010)."""

from __future__ import annotations

import httpx
import pytest

from api.main import create_app
from api.routes.growth import KEPT_GROWTH
from meridian_core.db import dispose_engines

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def client():
    KEPT_GROWTH.forget()
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    KEPT_GROWTH.forget()
    await dispose_engines()


async def test_it_opens_on_thirty_days(client) -> None:
    body = (await client.get("/api/explore/growth")).json()
    assert body["days"] == 30
    assert len(body["daily"]) == 30
    assert body["zone"]


@pytest.mark.parametrize(("window", "days"), [("7d", 7), ("all", None)])
async def test_the_other_windows(client, window, days) -> None:
    body = (await client.get("/api/explore/growth", params={"range": window})).json()
    assert body["days"] == days
    if days:
        assert len(body["daily"]) == days


async def test_an_unknown_window_is_refused(client) -> None:
    assert (await client.get("/api/explore/growth", params={"range": "90d"})).status_code == 422


async def test_a_topic_filter_is_the_topics_shown(client) -> None:
    body = (
        await client.get("/api/explore/growth", params=[("topic", "zz-b"), ("topic", "zz-a")])
    ).json()
    assert body["topics"] == ["zz-a", "zz-b"]


async def test_the_explore_route_takes_no_writes(client) -> None:
    assert (await client.post("/api/explore/growth")).status_code == 405
