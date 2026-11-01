"""Who is asking, people's permissions, and claiming a fresh instance (`B-252`, ADR 0024).

`/api/me` is what the web app shows and hides by; `/api/admin/people` needs `access.manage`;
`/api/setup/claim` works only while nobody can manage access. See
docs/features/api-and-access.md#permissions.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from meridian_core.db import session_ro
from meridian_core.models import Grant
from meridian_core.people import (
    GrantRefused,
    claim,
    create_grant,
    setup_code,
    setup_needed,
    update_grant,
)
from meridian_core.permissions import (
    PERMISSIONS,
    PROFILE_PERMISSIONS,
    UnknownPermission,
    effective,
    owner_emails,
)
from meridian_core.schemas.people import (
    MeRead,
    PeopleRead,
    PermissionRead,
    PersonAdd,
    PersonEdit,
    PersonRead,
    SetupClaim,
)

from ..deps import CanManageAccess, CurrentCaller, WriteSession, identity_is_checked

me_router = APIRouter(prefix="/api", tags=["access"])
router = APIRouter(prefix="/api/admin", tags=["admin"])


def _ordered(names) -> list[str]:
    wanted = set(names)
    return [p.name for p in PERMISSIONS if p.name in wanted]


@me_router.get("/me", response_model=MeRead)
async def me(caller: CurrentCaller) -> MeRead:
    """The caller and their permissions; also whether the instance waits to be claimed."""
    needed = False
    if identity_is_checked() and not caller.owner:
        async with session_ro() as sess:
            needed = await setup_needed(sess)
        if needed:
            setup_code()  # written to the log the first time it is needed
    return MeRead(
        subject=caller.subject,
        profile=caller.profile,
        owner=caller.owner,
        permissions=_ordered(caller.permissions),
        setup_needed=needed,
    )


@me_router.post("/setup/claim", response_model=MeRead)
async def claim_instance(body: SetupClaim, caller: CurrentCaller, sess: WriteSession) -> MeRead:
    """Become the person who manages access, with the code from the server's log."""
    if not identity_is_checked() or not caller.subject:
        raise HTTPException(status_code=409, detail="This instance checks no identity to grant.")
    try:
        grant = await claim(sess, caller.subject, body.code)
    except GrantRefused as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await sess.commit()
    permissions = effective(grant.profile, grant.allow, grant.deny)
    return MeRead(
        subject=grant.subject,
        profile=grant.profile,
        owner=False,
        permissions=_ordered(permissions),
        setup_needed=False,
    )


def _state(grant: Grant, now: dt.datetime) -> str:
    if grant.revoked:
        return "revoked"
    if grant.expires_at is not None and grant.expires_at <= now:
        return "expired"
    return "active"


def _person(grant: Grant, now: dt.datetime) -> PersonRead:
    return PersonRead(
        grant_id=grant.grant_id,
        subject=grant.subject,
        profile=grant.profile,
        owner=False,
        allow=list(grant.allow or []),
        deny=list(grant.deny or []),
        permissions=_ordered(effective(grant.profile, grant.allow, grant.deny)),
        expires_at=grant.expires_at,
        state=_state(grant, now),  # type: ignore[arg-type]
        note=grant.note,
    )


async def _people(sess, *, revoked: bool) -> PeopleRead:
    now = dt.datetime.now(dt.UTC)
    query = select(Grant).where(Grant.subject_kind == "person").order_by(Grant.subject)
    if not revoked:
        query = query.where(~Grant.revoked)
    grants = (await sess.scalars(query)).all()
    owners = [
        PersonRead(
            grant_id=None,
            subject=email,
            profile="owner",
            owner=True,
            allow=[],
            deny=[],
            permissions=[p.name for p in PERMISSIONS],
            expires_at=None,
            state="active",
        )
        for email in sorted(owner_emails())
    ]
    return PeopleRead(
        people=owners + [_person(g, now) for g in grants],
        catalogue=[PermissionRead(**p.__dict__) for p in PERMISSIONS],
        profiles={name: _ordered(held) for name, held in PROFILE_PERMISSIONS.items()},
    )


@router.get("/people", response_model=PeopleRead)
async def list_people(_: CanManageAccess, sess: WriteSession, revoked: bool = False) -> PeopleRead:
    """Owners from the environment, then every person's grant."""
    return await _people(sess, revoked=revoked)


@router.post("/people", response_model=PersonRead, status_code=201)
async def add_person(body: PersonAdd, caller: CanManageAccess, sess: WriteSession) -> PersonRead:
    try:
        grant = await create_grant(
            sess,
            subject=body.subject,
            actor=caller.subject or "local",
            profile=body.profile,
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=body.days),
            allow=body.allow,
            deny=body.deny,
            note=body.note,
        )
    except (GrantRefused, UnknownPermission) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await sess.commit()
    return _person(grant, dt.datetime.now(dt.UTC))


@router.patch("/people/{grant_id}", response_model=PersonRead)
async def edit_person(
    grant_id: int, body: PersonEdit, caller: CanManageAccess, sess: WriteSession
) -> PersonRead:
    grant = await sess.get(Grant, grant_id)
    if grant is None or grant.subject_kind != "person":
        raise HTTPException(status_code=404, detail=f"No person grant {grant_id}.")
    try:
        await update_grant(
            sess,
            grant,
            actor=caller.subject or "local",
            profile=body.profile,
            allow=body.allow,
            deny=body.deny,
            expires_at=(
                dt.datetime.now(dt.UTC) + dt.timedelta(days=body.days)
                if body.days is not None
                else None
            ),
            revoked=body.revoked,
        )
    except (GrantRefused, UnknownPermission) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await sess.commit()
    return _person(grant, dt.datetime.now(dt.UTC))
