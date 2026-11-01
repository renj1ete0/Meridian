"""What a person may do in the web app (`B-252`, ADR 0024).

A permission names one area and one verb. A grant's profile is a preset set of them, and its
``allow`` and ``deny`` switch single permissions on or off for that person. Owners, named in
``MERIDIAN_OWNER_EMAILS``, hold every permission. See docs/features/api-and-access.md#permissions.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Iterable


@dataclasses.dataclass(frozen=True)
class Permission:
    """One permission, as the API checks it and the Admin page shows it."""

    name: str
    label: str
    describes: str
    group: str


#: Every permission, in the order the Admin page lists them. A name is stored in grants and
#: held to this list by a CHECK; renaming one needs a migration.
PERMISSIONS: tuple[Permission, ...] = (
    Permission("explore.read", "Read the corpus", "Find, sources, map, gaps, growth", "Reading"),
    Permission("admin.view", "Open Admin", "Status, runs, health; read only", "Reading"),
    Permission(
        "work.write", "Notes and saved views", "Annotations, views", "Writing their own work"
    ),
    Permission("chat.ask", "Ask", "A model answers; spends budget", "Writing their own work"),
    Permission(
        "topics.steer",
        "Topics and steering",
        "Weights, boosts, proposals, map",
        "Steering the crawl",
    ),
    Permission("seeds.add", "Seeds", "URLs and searches to crawl", "Steering the crawl"),
    Permission(
        "gazetteer.curate", "Gazetteer", "Approve, edit and reject terms", "Steering the crawl"
    ),
    Permission("duplicates.decide", "Duplicates", "Merge or keep apart", "Steering the crawl"),
    Permission(
        "graph.audit", "Graph audit", "Grade sampled relations and merges", "Steering the crawl"
    ),
    Permission("fetch_policy.edit", "Fetch policy", "Politeness and blocks", "The system"),
    Permission("budget.edit", "Budget", "Token and spend caps", "The system"),
    Permission("models.edit", "Models", "Agent registry and routing", "The system"),
    Permission("settings.edit", "Display settings", "Time zone and how dates read", "The system"),
    Permission(
        "access.manage", "People & permissions", "This page; assistant tokens", "The system"
    ),
)

NAMES: tuple[str, ...] = tuple(p.name for p in PERMISSIONS)
BY_NAME = {p.name: p for p in PERMISSIONS}

EXPLORE_READ = "explore.read"
ACCESS_MANAGE = "access.manage"

#: What each grant profile holds before its switches. `operator` is everything but managing
#: people: handing out access is the owner's, or a switch someone chose to give.
PROFILE_PERMISSIONS: dict[str, frozenset[str]] = {
    "reader": frozenset({"explore.read"}),
    "analyst": frozenset({"explore.read", "admin.view", "work.write", "chat.ask"}),
    "operator": frozenset(NAMES) - {ACCESS_MANAGE},
}

ALL = frozenset(NAMES)


class UnknownPermission(ValueError):
    """A permission name that is not in :data:`PERMISSIONS`."""


def checked(names: Iterable[str]) -> list[str]:
    """``names`` de-duplicated in catalogue order; refuses any not in the catalogue, by name."""
    wanted = set(names)
    unknown = sorted(wanted - ALL)
    if unknown:
        raise UnknownPermission(f"unknown permission(s): {', '.join(unknown)}")
    return [name for name in NAMES if name in wanted]


def effective(profile: str, allow: Iterable[str] = (), deny: Iterable[str] = ()) -> frozenset[str]:
    """What a grant holds: its profile's permissions, plus ``allow``, minus ``deny``.

    A permission both allowed and denied is denied: taking away is the safer reading.
    """
    base = PROFILE_PERMISSIONS.get(profile)
    if base is None:
        raise ValueError(f"unknown profile {profile!r}")
    return frozenset((base | set(allow)) - set(deny)) & ALL


def owner_emails(environ: dict[str, str] | None = None) -> frozenset[str]:
    """The owners named in ``MERIDIAN_OWNER_EMAILS`` (comma-separated), lower-cased."""
    raw = (os.environ if environ is None else environ).get("MERIDIAN_OWNER_EMAILS", "")
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())
