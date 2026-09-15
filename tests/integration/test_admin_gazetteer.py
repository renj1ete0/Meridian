"""`/api/admin/*` against a real Postgres (task P6-13, spec §12.6, §5.6).

Three things are worth checking here and none of them can be checked with a
double.

**The gate.** These are the only routes in the service that change anything, and
an instance that does not know who its callers are must refuse them. The failure
this prevents is a tunnel pointed at the API before Access is configured, which
is a deployment step and therefore a step somebody skips.

**The verdict.** Approving a term reports whether the matcher will actually load
it, computed against every other approved row — so a collision only appears once
its other half exists. That is a property of the table, not of the request.

**The tombstone.** A rejected term keeps its row, because the harvest reads the
same documents on every pass and would otherwise re-create it. A test that only
checked "reject returns 200" would pass against an implementation that deleted.

Fixtures commit, for `test_explore_api.py`'s reason: the app opens its own
connection, so anything written and not committed is invisible to the thing
under test.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import delete, select

from api.main import create_app
from meridian_core.db import dispose_engines
from meridian_core.models import GazetteerTerm

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def marker() -> str:
    """Scopes this run's rows. The dev database holds a seeded gazetteer, so an
    unscoped assertion about which terms come back would be an assertion about
    the seed file."""
    return f"Qxz{uuid.uuid4().hex[:10]}"


@pytest.fixture
def open_admin(monkeypatch) -> None:
    """An instance that has been told it is not exposed."""
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "true")
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def terms(session_for, marker: str):
    """A handful of this run's own rows, committed and cleaned up."""
    sess = await session_for("rw")
    made: dict[str, GazetteerTerm] = {}

    def add(key: str, canonical: str, **over) -> None:
        term = GazetteerTerm(
            canonical=canonical,
            aliases=over.pop("aliases", [marker[:4].upper()]),
            entity_type=over.pop("entity_type", "concept"),
            source=over.pop("source", "auto_acronym"),
            occurrence_count=over.pop("occurrence_count", 1),
            **over,
        )
        sess.add(term)
        made[key] = term

    add("waiting", f"{marker} Coordination Bureau", occurrence_count=7)
    add("other", f"{marker} Container Board", occurrence_count=2)
    add("approved", f"{marker} Transport Agency", aliases=None, approved=True, occurrence_count=9)
    await sess.commit()

    yield made

    await sess.execute(delete(GazetteerTerm).where(GazetteerTerm.canonical.like(f"{marker}%")))
    await sess.commit()


async def reload(session_for, term_id: int) -> GazetteerTerm:
    """Read a row back on a fresh connection — the app committed on another."""
    sess = await session_for("rw")
    await sess.rollback()
    return (await sess.scalars(select(GazetteerTerm).where(GazetteerTerm.term_id == term_id))).one()


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


async def test_admin_refuses_when_nobody_is_identified(client, monkeypatch) -> None:
    # The failure this prevents is a tunnel pointed at the API before Access is
    # configured. Open-unless-configured fails silently and in the wrong
    # direction: the symptom is nothing at all until somebody finds the hostname.
    monkeypatch.delenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)

    response = await client.get("/api/admin/gazetteer")

    assert response.status_code == 503


async def test_the_refusal_names_the_variables_that_fix_it(client, monkeypatch) -> None:
    # A 503 reading "service unavailable" sends whoever deployed it looking for
    # an outage. The cause is configuration and the message has to say so.
    monkeypatch.delenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)

    detail = (await client.get("/api/admin/gazetteer")).json()["detail"]

    assert "CF_ACCESS_TEAM_DOMAIN" in detail
    assert "MERIDIAN_ADMIN_ALLOW_ANONYMOUS" in detail


async def test_a_typo_in_the_opt_out_is_not_consent(client, monkeypatch) -> None:
    # A variable set to something that is not "yes" must not read as yes. The
    # classic version of this bug accepts "false" as truthy because it is a
    # non-empty string.
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "false")
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)

    assert (await client.get("/api/admin/gazetteer")).status_code == 503


async def test_access_configuration_alone_opens_admin(client, monkeypatch) -> None:
    # The production path: identity comes from a verified Access assertion, and
    # no opt-out is set. Both variables, because a team domain alone only says
    # some application on the team signed it (`P3-08`).
    monkeypatch.delenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "example.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "aud-tag")

    assert (await client.get("/api/admin/gazetteer")).status_code != 503


# --------------------------------------------------------------------------
# The queue
# --------------------------------------------------------------------------


async def test_pending_excludes_what_has_already_been_decided(
    client, open_admin, terms, marker
) -> None:
    body = (await client.get("/api/admin/gazetteer?state=pending&limit=200")).json()
    names = [row["term"]["canonical"] for row in body["rows"]]

    assert f"{marker} Coordination Bureau" in names
    assert f"{marker} Transport Agency" not in names


async def test_the_counts_are_not_filtered(client, open_admin, terms) -> None:
    # `P6-08`'s reasoning: a filtered list whose counts are also filtered cannot
    # tell a curator that the forty terms they came for are one tab over, so
    # they conclude there are none.
    pending = (await client.get("/api/admin/gazetteer?state=pending")).json()
    rejected = (await client.get("/api/admin/gazetteer?state=rejected")).json()

    assert pending["approved"] == rejected["approved"] > 0
    assert pending["pending"] == rejected["pending"] > 0


async def test_the_queue_is_ordered_by_corroboration(client, open_admin, terms, marker) -> None:
    # The curator's own triage: a term seven documents defined the same way is
    # worth two seconds and one document's typo is worth none.
    body = (await client.get("/api/admin/gazetteer?state=pending&limit=200")).json()
    ours = [row for row in body["rows"] if row["term"]["canonical"].startswith(marker)]

    assert [row["term"]["occurrence_count"] for row in ours] == sorted(
        (row["term"]["occurrence_count"] for row in ours), reverse=True
    )


# --------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------


async def test_approving_reports_that_the_term_will_match(client, open_admin, terms) -> None:
    term_id = terms["waiting"].term_id

    row = (await client.post(f"/api/admin/gazetteer/{term_id}/approve")).json()

    assert row["term"]["approved"] is True
    assert row["will_load"] is True
    assert row["withheld_reason"] is None


async def test_a_waiting_term_is_reported_as_not_matching_yet(client, open_admin, terms) -> None:
    body = (await client.get("/api/admin/gazetteer?state=pending&limit=200")).json()
    row = next(r for r in body["rows"] if r["term"]["term_id"] == terms["waiting"].term_id)

    assert row["will_load"] is False
    assert row["withheld_reason"] == "unapproved"


async def test_approving_into_a_collision_says_so_and_names_the_other_row(
    client, open_admin, terms
) -> None:
    # The whole reason the verdict is reported with the decision. Two rows share
    # an alias, so neither is used — and a curator who approves the second and is
    # told nothing has no way ever to discover that the first stopped matching.
    first = terms["waiting"].term_id
    second = terms["other"].term_id

    await client.post(f"/api/admin/gazetteer/{first}/approve")
    row = (await client.post(f"/api/admin/gazetteer/{second}/approve")).json()

    assert row["term"]["approved"] is True
    assert row["will_load"] is True, "the canonical is unambiguous and still matches"
    assert row["withheld_reason"] == "collision"
    assert first in row["collides_with"]


async def test_the_collision_is_visible_from_the_other_row_too(client, open_admin, terms) -> None:
    # A collision is a fact about two rows. Reporting it only on whichever was
    # approved second would make the first one's silence permanent.
    first = terms["waiting"].term_id
    second = terms["other"].term_id
    await client.post(f"/api/admin/gazetteer/{first}/approve")
    await client.post(f"/api/admin/gazetteer/{second}/approve")

    body = (await client.get("/api/admin/gazetteer?state=approved&limit=200")).json()
    row = next(r for r in body["rows"] if r["term"]["term_id"] == first)

    assert row["withheld_reason"] == "collision"
    assert second in row["collides_with"]


# --------------------------------------------------------------------------
# The tombstone
# --------------------------------------------------------------------------


async def test_rejecting_keeps_the_row(client, open_admin, terms, session_for) -> None:
    # Not deleted, on purpose. §5.6's harvest reads the same documents on every
    # pass, so a deleted row is re-created by the next one and the queue refills
    # with exactly what a curator already turned down.
    term_id = terms["waiting"].term_id

    row = (await client.post(f"/api/admin/gazetteer/{term_id}/reject")).json()
    stored = await reload(session_for, term_id)

    assert row["term"]["rejected_at"] is not None
    assert stored.approved is False
    assert stored.rejected_at is not None


async def test_a_rejected_term_leaves_the_waiting_queue(client, open_admin, terms) -> None:
    term_id = terms["waiting"].term_id
    await client.post(f"/api/admin/gazetteer/{term_id}/reject")

    body = (await client.get("/api/admin/gazetteer?state=pending&limit=200")).json()

    assert all(row["term"]["term_id"] != term_id for row in body["rows"])


async def test_restoring_returns_it_undecided_rather_than_approved(
    client, open_admin, terms, session_for
) -> None:
    # A judgement made on two occurrences is worth revisiting at twenty, and the
    # second look should start from "undecided" rather than from the answer being
    # reconsidered.
    term_id = terms["waiting"].term_id
    await client.post(f"/api/admin/gazetteer/{term_id}/reject")

    row = (await client.post(f"/api/admin/gazetteer/{term_id}/restore")).json()
    stored = await reload(session_for, term_id)

    assert row["term"]["approved"] is False
    assert stored.rejected_at is None


async def test_approving_a_rejected_term_clears_the_tombstone(
    client, open_admin, terms, session_for
) -> None:
    # Otherwise the row is approved *and* rejected: it would load into the
    # matcher while the harvest went on treating it as thrown away.
    term_id = terms["waiting"].term_id
    await client.post(f"/api/admin/gazetteer/{term_id}/reject")

    await client.post(f"/api/admin/gazetteer/{term_id}/approve")
    stored = await reload(session_for, term_id)

    assert stored.approved is True
    assert stored.rejected_at is None


# --------------------------------------------------------------------------
# Correcting a term
# --------------------------------------------------------------------------


async def test_an_omitted_field_is_left_alone(client, open_admin, terms, session_for) -> None:
    # `exclude_unset` is what makes clearing a field possible at all. Without the
    # distinction, a PATCH sending only `entity_type` would null out every other
    # column — silently, and on the row a curator was about to approve.
    term_id = terms["waiting"].term_id
    await client.patch(f"/api/admin/gazetteer/{term_id}", json={"jurisdiction": "SG"})

    await client.patch(f"/api/admin/gazetteer/{term_id}", json={"entity_type": "agency"})
    stored = await reload(session_for, term_id)

    assert stored.entity_type == "agency"
    assert stored.jurisdiction == "SG"


async def test_an_explicit_null_clears_the_field(client, open_admin, terms, session_for) -> None:
    term_id = terms["waiting"].term_id
    await client.patch(f"/api/admin/gazetteer/{term_id}", json={"jurisdiction": "SG"})

    await client.patch(f"/api/admin/gazetteer/{term_id}", json={"jurisdiction": None})
    stored = await reload(session_for, term_id)

    assert stored.jurisdiction is None


async def test_blank_aliases_are_dropped_rather_than_stored(
    client, open_admin, terms, session_for
) -> None:
    # A blank alias produces no pattern and cannot be told apart in a UI from a
    # row that has none, so it is a row that looks edited and matches nothing.
    term_id = terms["waiting"].term_id

    await client.patch(f"/api/admin/gazetteer/{term_id}", json={"aliases": ["  ", "ABC", ""]})
    stored = await reload(session_for, term_id)

    assert stored.aliases == ["ABC"]


async def test_an_unknown_entity_type_is_refused(client, open_admin, terms) -> None:
    # §2.6: all writes validate server-side. The five types are a CHECK
    # constraint, and a value that reached Postgres would fail the insert after
    # the request had already reported success to the curator.
    term_id = terms["waiting"].term_id

    response = await client.patch(
        f"/api/admin/gazetteer/{term_id}", json={"entity_type": "organisation"}
    )

    assert response.status_code == 422


async def test_an_unknown_field_is_refused(client, open_admin, terms) -> None:
    # `extra="forbid"`. A typo'd key silently ignored is an edit the curator
    # believes they made.
    term_id = terms["waiting"].term_id

    response = await client.patch(f"/api/admin/gazetteer/{term_id}", json={"canonicaal": "x"})

    assert response.status_code == 422


async def test_an_empty_canonical_is_refused(client, open_admin, terms) -> None:
    term_id = terms["waiting"].term_id

    response = await client.patch(f"/api/admin/gazetteer/{term_id}", json={"canonical": ""})

    assert response.status_code == 422


async def test_a_term_that_does_not_exist_is_a_404(client, open_admin) -> None:
    assert (await client.post("/api/admin/gazetteer/99999999/approve")).status_code == 404


# --------------------------------------------------------------------------
# The role boundary (§12.6)
# --------------------------------------------------------------------------


def routes_of(app) -> list[tuple[str, frozenset[str]]]:
    """Every mounted path and its methods.

    FastAPI 0.141 wraps an included router in `_IncludedRouter`, which exposes
    neither `path` nor `routes` — so the obvious traversal finds nothing and
    every assertion built on it passes for the wrong reason. It did, until
    `test_the_walk_actually_finds_routes` was added below.
    """
    found: list[tuple[str, frozenset[str]]] = []
    for route in app.routes:
        subs = getattr(getattr(route, "original_router", None), "routes", None) or [route]
        for sub in subs:
            path = getattr(sub, "path", "")
            methods = frozenset(getattr(sub, "methods", frozenset()) or frozenset())
            if path:
                found.append((path, methods))
    return found


def test_the_walk_actually_finds_routes() -> None:
    # Guards every assertion below. A traversal that returns nothing makes them
    # all vacuous, and nothing about a passing suite would say so.
    paths = [path for path, _ in routes_of(create_app())]

    assert "/api/explore/search" in paths
    assert "/api/admin/gazetteer" in paths


def test_no_route_under_explore_accepts_a_write() -> None:
    # §12.6 makes the prefix the role boundary so that adding auth later is
    # middleware on a path rather than a refactor of every handler. That only
    # holds if nothing under `/api/explore` ever mutates — and the moment a
    # writable session exists in this service, the cheapest way for it to leak
    # across is a POST added to the wrong router.
    offences = [
        (path, sorted(methods))
        for path, methods in routes_of(create_app())
        if path.startswith("/api/explore") and methods - {"GET", "HEAD", "OPTIONS"}
    ]

    assert offences == []


async def test_every_admin_route_is_behind_the_gate(client, monkeypatch) -> None:
    # A completeness probe rather than a spot check: the gate is a dependency so
    # that a new route cannot be added without it, and this is what notices if
    # one is. Driven through the app rather than read off the signatures, so it
    # holds however the dependency is expressed.
    monkeypatch.delenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)

    admin = [(p, m) for p, m in routes_of(create_app()) if p.startswith("/api/admin")]
    assert admin, "no admin routes found; this test would pass on an empty app"

    served = []
    for path, methods in admin:
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            response = await client.request(method, path.replace("{term_id}", "1"))
            if response.status_code != 503:
                served.append((method, path, response.status_code))

    assert served == []
