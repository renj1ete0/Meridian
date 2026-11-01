"""Every route names the permission it needs (task B-252, ADR 0024).

A completeness probe: a route added without a ``Can*`` annotation would be open to anyone
Access lets in, and nothing else would notice. The catalogue is also held against the
migration's written-out copy and the profiles' rules.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
from fastapi.routing import APIRoute

from meridian_core.permissions import (
    ACCESS_MANAGE,
    ALL,
    NAMES,
    PERMISSIONS,
    PROFILE_PERMISSIONS,
    UnknownPermission,
    checked,
    effective,
    owner_emails,
)

#: Routes that need no permission, and why.
EXEMPT = {
    "/api/me": "says who is asking, including to someone with no access",
    "/api/setup/claim": "the way in while nobody can manage access; refuses once someone can",
}

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def routes() -> list:
    """Every route of the app, including those behind FastAPI's lazily included routers.

    `app.routes` lists an included router as one wrapper (FastAPI 0.14x); walking only the
    `APIRoute`s found `/health` and nothing else, so an earlier draft passed vacuously.
    """
    from api.main import create_app

    found = []
    for route in create_app().routes:
        if isinstance(route, APIRoute):
            found.append(route)
        elif hasattr(route, "effective_route_contexts"):
            found.extend(route.effective_route_contexts())
    assert len(found) > 50, f"found only {len(found)} routes; the walk is broken"
    return found


def permissions_of(route) -> list[str]:
    found: list[str] = []

    def walk(dependant) -> None:
        for dep in dependant.dependencies:
            name = getattr(dep.call, "permission", None)
            if name:
                found.append(name)
            walk(dep)

    walk(route.dependant)
    return found


def test_every_api_route_names_one_permission_or_is_exempt_for_a_reason(routes) -> None:
    missing, doubled = [], []
    for route in routes:
        if not route.path.startswith("/api/") or route.path in EXEMPT:
            continue
        found = set(permissions_of(route))
        if not found:
            missing.append(f"{sorted(route.methods)} {route.path}")
        elif len(found) > 1:
            doubled.append(f"{route.path}: {sorted(found)}")
    assert not missing, f"routes that name no permission: {missing}"
    assert not doubled, f"routes that name more than one: {doubled}"


def test_the_exempt_routes_exist_and_name_none(routes) -> None:
    """An exemption for a route that was renamed would exempt nothing and hide the rename."""
    by_path = {r.path: r for r in routes}
    for path in EXEMPT:
        assert path in by_path, path
        assert permissions_of(by_path[path]) == [], path


def test_admin_writes_never_ride_on_read_permissions(routes) -> None:
    """A change under `/api/admin` behind `explore.read` or `admin.view` would let every
    reader change the system."""
    for route in routes:
        if not route.path.startswith("/api/admin") or not (route.methods - {"GET", "HEAD"}):
            continue
        assert not set(permissions_of(route)) & {"explore.read", "admin.view"}, route.path


def test_reader_routes_need_reading_and_nothing_more(routes) -> None:
    for route in routes:
        if route.path.startswith("/api/explore"):
            assert permissions_of(route) == ["explore.read"], route.path


def test_the_migration_holds_the_same_catalogue() -> None:
    """Drift: the CHECK is built from the migration's copy, the API from the code's."""
    path = next((ROOT / "migrations" / "versions").glob("*b252a1c2d3e4*.py"))
    spec = importlib.util.spec_from_file_location("b252", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert tuple(module.PERMISSIONS) == NAMES


def test_permission_names_are_unique_and_described() -> None:
    assert len(set(NAMES)) == len(NAMES)
    assert all(p.label and p.describes and p.group for p in PERMISSIONS)


def test_profiles_are_presets_of_known_permissions() -> None:
    assert PROFILE_PERMISSIONS["reader"] == {"explore.read"}
    assert ACCESS_MANAGE not in PROFILE_PERMISSIONS["operator"]
    assert PROFILE_PERMISSIONS["operator"] | {ACCESS_MANAGE} == ALL
    assert PROFILE_PERMISSIONS["reader"] < PROFILE_PERMISSIONS["analyst"]
    assert PROFILE_PERMISSIONS["analyst"] < PROFILE_PERMISSIONS["operator"]


def test_a_permission_both_allowed_and_denied_is_denied() -> None:
    assert "seeds.add" not in effective("reader", allow=["seeds.add"], deny=["seeds.add"])
    assert effective("analyst", allow=["seeds.add"]) == PROFILE_PERMISSIONS["analyst"] | {
        "seeds.add"
    }


def test_unknown_names_are_refused_by_name() -> None:
    with pytest.raises(UnknownPermission, match="root.everything"):
        checked(["explore.read", "root.everything"])
    with pytest.raises(ValueError, match="profile"):
        effective("superuser")


def test_checked_keeps_catalogue_order_without_repeats() -> None:
    assert checked(["access.manage", "explore.read", "explore.read"]) == [
        "explore.read",
        "access.manage",
    ]


def test_owners_are_read_from_the_environment_lower_cased() -> None:
    assert owner_emails({"MERIDIAN_OWNER_EMAILS": " A@x.test, b@x.test ,,"}) == {
        "a@x.test",
        "b@x.test",
    }
    assert owner_emails({}) == frozenset()


def test_the_web_labels_every_permission_as_the_catalogue_does() -> None:
    """Drift: Admin names a missing permission by the web's copy of its label."""
    import re

    source = (ROOT / "web" / "src" / "lib" / "permissionLabels.ts").read_text()
    web = dict(re.findall(r"'([a-z_.]+)': '([^']*)'", source))
    assert web == {p.name: p.label for p in PERMISSIONS}


def test_each_admin_section_disables_on_a_permission_some_write_route_needs(routes) -> None:
    """Drift: a section's `edits` (what B-258 disables its controls on) is a write's permission.

    A section gated on a permission no write route requires would disable controls the server
    lets through, and leave enabled the ones it refuses.
    """
    import re

    source = (ROOT / "web" / "src" / "admin" / "sections.ts").read_text()
    edits = set(re.findall(r"edits: '([a-z_.]+)'", source))
    assert len(edits) > 5, "the parse found too few sections; the pattern is broken"
    written = {
        name
        for route in routes
        if route.path.startswith("/api/") and route.methods - {"GET", "HEAD"}
        for name in permissions_of(route)
    }
    assert edits <= written, f"no write route needs: {sorted(edits - written)}"
