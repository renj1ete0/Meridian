"""Saved views (task P6-09, spec §12.5, §12.6).

§12.5: "a filter set plus focus node, named and re-openable."

**A table rather than `localStorage`**, and the difference is the point. A saved
view is a piece of research method — the slice somebody decided was worth
returning to — so it survives a cleared cache, reaches a second device, and
travels in the database snapshot that is supposed to be the whole system.
`P6-11`'s last-visit stamp is in `localStorage` for the opposite reason: it is
per-reader, per-device, and worthless to anybody else.

**Reads are under `/api/explore` and every write is under `/api/admin`**, which
looks inconsistent for something a reader creates while reading. §12.6 splits the
prefixes by mutation rather than by audience, and the consequence here is the one
that matters: saved views are shared state with no per-viewer scoping, so a guest
on a shared instance must be able to open the owner's views and must not be able
to add to them.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from api.main import create_app
from meridian_core.db import dispose_engines
from meridian_core.models import SavedView

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def name() -> str:
    return f"view-{uuid.uuid4().hex[:10]}"


@pytest.fixture
def open_admin(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "true")


@pytest.fixture
async def client():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def clean(session_for, name: str):
    sess = await session_for("rw")
    yield sess
    await sess.rollback()
    await sess.execute(delete(SavedView).where(SavedView.name.like("view-%")))
    await sess.commit()


async def stored(session_for, view_id: int) -> SavedView | None:
    sess = await session_for("rw")
    await sess.rollback()
    return (
        await sess.scalars(select(SavedView).where(SavedView.view_id == view_id))
    ).one_or_none()


async def save(client, name: str, **body) -> dict:
    return (await client.post("/api/admin/views", json={"name": name, **body})).json()


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------


async def test_a_view_keeps_its_query_and_its_filters(client, open_admin, clean, name) -> None:
    body = await save(
        client, name, query="walkability", filters={"source_tiers": ["government"]}
    )

    assert body["query"] == "walkability"
    assert body["filters"] == {"source_tiers": ["government"]}


async def test_a_filter_the_search_cannot_apply_is_refused(client, open_admin, clean, name) -> None:
    # A view that silently drops a filter when reopened is worse than one that
    # refuses to save: the reader gets a result set they believe is narrowed and
    # is not, and nothing says so.
    response = await client.post(
        "/api/admin/views", json={"name": name, "filters": {"not_a_filter": True}}
    )

    assert response.status_code == 422
    assert "not_a_filter" in response.json()["detail"]


async def test_a_real_filter_set_is_accepted(client, open_admin, clean, name) -> None:
    # The converse: the check must not be a blanket refusal of anything with
    # keys in it.
    response = await client.post(
        "/api/admin/views",
        json={"name": name, "filters": {"topics": ["walkability"], "include_junk": True}},
    )

    assert response.status_code == 201


async def test_two_views_cannot_share_a_name(client, open_admin, clean, name) -> None:
    # The name is how somebody refers to it. Two called "contested walkability"
    # is a list nobody can use.
    await save(client, name)

    response = await client.post("/api/admin/views", json={"name": name})

    assert response.status_code == 409
    assert name in response.json()["detail"]


async def test_an_unnamed_view_is_refused(client, open_admin, clean) -> None:
    assert (await client.post("/api/admin/views", json={"name": ""})).status_code == 422


async def test_an_unknown_field_is_refused(client, open_admin, clean, name) -> None:
    response = await client.post("/api/admin/views", json={"name": name, "focus": 3})

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Reading (§12.6's boundary)
# --------------------------------------------------------------------------


async def test_views_are_readable_from_the_explore_surface(
    client, open_admin, clean, name
) -> None:
    # A guest on a shared instance can open the owner's views. That is the whole
    # reason reads and writes sit on different prefixes here.
    await save(client, name)

    body = (await client.get("/api/explore/views")).json()

    assert name in [view["name"] for view in body["views"]]


async def test_saving_requires_admin(client, clean, name, monkeypatch) -> None:
    # And the other half: a guest cannot add to them. Without per-viewer scoping
    # a guest's views would be indistinguishable from the owner's.
    monkeypatch.delenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)

    assert (await client.post("/api/admin/views", json={"name": name})).status_code == 503


async def test_the_most_recently_opened_comes_first(client, open_admin, clean, name) -> None:
    older = await save(client, f"{name}-a")
    newer = await save(client, f"{name}-b")
    await client.post(f"/api/admin/views/{older['view_id']}/opened")
    await client.post(f"/api/admin/views/{newer['view_id']}/opened")

    names = [v["name"] for v in (await client.get("/api/explore/views")).json()["views"]]

    assert names.index(f"{name}-b") < names.index(f"{name}-a")


async def test_a_view_never_opened_is_listed_last_not_hidden(
    client, open_admin, clean, name
) -> None:
    # Somebody saved it and did not come back. Disappearing it would be the
    # system deciding that was a mistake.
    await save(client, f"{name}-never")
    opened = await save(client, f"{name}-opened")
    await client.post(f"/api/admin/views/{opened['view_id']}/opened")

    names = [v["name"] for v in (await client.get("/api/explore/views")).json()["views"]]

    assert f"{name}-never" in names
    assert names.index(f"{name}-opened") < names.index(f"{name}-never")


async def test_listing_views_does_not_count_as_opening_one(
    client, open_admin, clean, name, session_for
) -> None:
    # Listing is not returning. A read that wrote would also put
    # `/api/explore/views` on the wrong side of §12.6's boundary.
    saved = await save(client, name)

    await client.get("/api/explore/views")

    assert (await stored(session_for, saved["view_id"])).last_opened_at is None


# --------------------------------------------------------------------------
# Editing and removing
# --------------------------------------------------------------------------


async def test_a_view_can_be_renamed(client, open_admin, clean, name, session_for) -> None:
    saved = await save(client, name)

    await client.patch(f"/api/admin/views/{saved['view_id']}", json={"name": f"{name}-renamed"})

    assert (await stored(session_for, saved["view_id"])).name == f"{name}-renamed"


async def test_renaming_onto_another_view_is_refused(client, open_admin, clean, name) -> None:
    first = await save(client, f"{name}-a")
    await save(client, f"{name}-b")

    response = await client.patch(
        f"/api/admin/views/{first['view_id']}", json={"name": f"{name}-b"}
    )

    assert response.status_code == 409


async def test_renaming_a_view_to_its_own_name_is_allowed(client, open_admin, clean, name) -> None:
    # The self-clash. A uniqueness check that forgets to exclude the row being
    # edited refuses every no-op save, which reads as the form being broken.
    saved = await save(client, name)

    response = await client.patch(
        f"/api/admin/views/{saved['view_id']}", json={"name": name, "note": "unchanged"}
    )

    assert response.status_code == 200


async def test_deleting_removes_it(client, open_admin, clean, name, session_for) -> None:
    # The one delete on the admin surface, and it is right: a view holds no
    # evidence and cites nothing, so a tombstone would only clutter the list it
    # exists to be read from.
    saved = await save(client, name)

    response = await client.delete(f"/api/admin/views/{saved['view_id']}")

    assert response.status_code == 204
    assert await stored(session_for, saved["view_id"]) is None


async def test_deleting_something_that_is_not_there_is_a_404(client, open_admin, clean) -> None:
    assert (await client.delete("/api/admin/views/99999999")).status_code == 404
