"""People and permissions at the API boundary (`B-252`, ADR 0024)."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .enums import GrantProfile

#: A permission name; checked against the catalogue on write.
PermissionName = str


class PermissionRead(BaseModel):
    """One permission, as the Admin page lists it."""

    name: PermissionName
    label: str
    describes: str
    group: str


class MeRead(BaseModel):
    """Who the web app is talking to, and what to show them."""

    #: The verified Access identity; None on an instance that checks none.
    subject: str | None
    #: "owner", a grant profile, or None for someone with no access.
    profile: str | None
    owner: bool
    permissions: list[PermissionName]
    #: True when nobody can manage access yet, so the claim form is shown.
    setup_needed: bool


class PersonRead(BaseModel):
    """One person's grant: the profile, its switches, and what they add up to."""

    grant_id: int | None
    subject: str
    #: "owner" for someone named in the environment.
    profile: str
    owner: bool
    allow: list[PermissionName]
    deny: list[PermissionName]
    permissions: list[PermissionName]
    expires_at: dt.datetime | None
    #: "active", "expired" or "revoked".
    state: Literal["active", "expired", "revoked"]
    note: str | None = None


class PeopleRead(BaseModel):
    people: list[PersonRead]
    catalogue: list[PermissionRead]
    #: What each profile holds before switches.
    profiles: dict[str, list[PermissionName]]


class PersonAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=3, max_length=254)
    profile: GrantProfile = "reader"
    #: Days until it ends; a person's access always ends.
    days: int = Field(default=365, ge=1, le=3650)
    allow: list[PermissionName] = Field(default_factory=list)
    deny: list[PermissionName] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=500)


class PersonEdit(BaseModel):
    """A partial change: an omitted field is kept."""

    model_config = ConfigDict(extra="forbid")

    profile: GrantProfile | None = None
    allow: list[PermissionName] | None = None
    deny: list[PermissionName] | None = None
    days: int | None = Field(default=None, ge=1, le=3650)
    revoked: bool | None = None


class SetupClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=4, max_length=32)
