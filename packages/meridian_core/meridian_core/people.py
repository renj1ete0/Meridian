"""People, their grants and what they may do in the web app (`B-252`, ADR 0024).

Resolves a verified Access identity to its permissions, writes grants with a record of who
changed them, and holds the one-time code that claims a fresh instance. The API's gate
(`api.deps`) calls :func:`resolve`; see docs/features/api-and-access.md#permissions.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hmac
import secrets

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import Grant, GrantChange
from .permissions import ACCESS_MANAGE, ALL, EXPLORE_READ, checked, effective, owner_emails

log = get_logger(__name__)

#: How long a grant made by the setup code lasts; a person grant must expire.
SETUP_GRANT_DAYS = 365

#: The subject recorded for a grant made by the setup code.
SETUP_ACTOR = "setup"


@dataclasses.dataclass(frozen=True)
class Caller:
    """Who is asking, and what they may do."""

    subject: str | None
    permissions: frozenset[str]
    owner: bool = False
    profile: str | None = None
    grant_id: int | None = None

    def may(self, permission: str) -> bool:
        return permission in self.permissions


def owner(subject: str) -> Caller:
    return Caller(subject=subject, permissions=ALL, owner=True, profile="owner")


#: Who a request is when the instance has no identity to check: reader pages stay open,
#: as before `B-252`; Admin stays closed unless the operator opted out.
NOBODY = Caller(subject=None, permissions=frozenset({EXPLORE_READ}))


def live(now: dt.datetime | None = None):
    """The condition for a grant that is in force: not revoked, not expired."""
    now = now or dt.datetime.now(dt.UTC)
    return (~Grant.revoked) & or_(Grant.expires_at.is_(None), Grant.expires_at > now)


async def resolve(
    sess: AsyncSession,
    subject: str | None,
    *,
    identity_checked: bool,
    anonymous_admin: bool,
    environ: dict[str, str] | None = None,
) -> Caller:
    """The caller for a request.

    ``identity_checked`` is whether Access verified ``subject``. Without it there is no one
    to look up: an owner when the operator opted out of identity
    (``MERIDIAN_ADMIN_ALLOW_ANONYMOUS``), otherwise a reader of the reader pages.
    """
    if not identity_checked:
        return owner("local") if anonymous_admin else NOBODY
    if not subject:
        return Caller(subject=None, permissions=frozenset())
    key = subject.strip().lower()
    if key in owner_emails(environ):
        return owner(key)
    grant = await sess.scalar(
        select(Grant).where(Grant.subject == key, live()).order_by(Grant.grant_id.desc()).limit(1)
    )
    if grant is None:
        return Caller(subject=key, permissions=frozenset())
    return Caller(
        subject=key,
        permissions=effective(grant.profile, grant.allow or (), grant.deny or ()),
        profile=grant.profile,
        grant_id=grant.grant_id,
    )


# ---------------------------------------------------------------------------
# Writing grants
# ---------------------------------------------------------------------------


class GrantRefused(ValueError):
    """A grant change that is not allowed, by name."""


def snapshot(grant: Grant) -> dict:
    """The parts of a grant that decide access, as a change record holds them."""
    return {
        "subject": grant.subject,
        "profile": grant.profile,
        "allow": list(grant.allow or []),
        "deny": list(grant.deny or []),
        "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
        "revoked": bool(grant.revoked),
    }


async def _record(sess: AsyncSession, grant: Grant, actor: str, before: dict | None) -> None:
    await sess.flush()
    sess.add(
        GrantChange(grant_id=grant.grant_id, actor=actor, before=before, after=snapshot(grant))
    )
    await sess.flush()


async def create_grant(
    sess: AsyncSession,
    *,
    subject: str,
    actor: str,
    profile: str = "reader",
    expires_at: dt.datetime,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    note: str | None = None,
    environ: dict[str, str] | None = None,
) -> Grant:
    """Grant a person access; flushes, does not commit. Refuses an owner or a live duplicate."""
    key = subject.strip().lower()
    if "@" not in key:
        raise GrantRefused(f"not an email address: {subject!r}")
    if key in owner_emails(environ):
        raise GrantRefused(f"{key} is an owner, set in the server's environment")
    if expires_at <= dt.datetime.now(dt.UTC):
        raise GrantRefused("the expiry is in the past")
    if await sess.scalar(select(Grant.grant_id).where(Grant.subject == key, live())):
        raise GrantRefused(f"{key} already has access; change that grant instead")
    grant = Grant(
        subject=key,
        subject_kind="person",
        profile=profile,
        expires_at=expires_at,
        allow=checked(allow or []),
        deny=checked(deny or []),
        note=note,
    )
    sess.add(grant)
    await _record(sess, grant, actor, None)
    return grant


async def update_grant(
    sess: AsyncSession,
    grant: Grant,
    *,
    actor: str,
    profile: str | None = None,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    expires_at: dt.datetime | None = None,
    revoked: bool | None = None,
) -> Grant:
    """Change a grant's access and record the change; flushes, does not commit.

    Refuses a change that leaves the actor unable to manage access through this grant, so
    nobody removes their own last way back (an owner is never affected).
    """
    before = snapshot(grant)
    if profile is not None:
        effective(profile)  # refuses an unknown profile
        grant.profile = profile
    if allow is not None:
        grant.allow = checked(allow)
    if deny is not None:
        grant.deny = checked(deny)
    if expires_at is not None:
        grant.expires_at = expires_at
    if revoked is not None:
        grant.revoked = revoked
    if grant.subject == actor and before != snapshot(grant):
        keeps = not grant.revoked and ACCESS_MANAGE in effective(
            grant.profile, grant.allow, grant.deny
        )
        if not keeps:
            raise GrantRefused("this change would take away your own access to this page")
    if before != snapshot(grant):
        await _record(sess, grant, actor, before)
    return grant


# ---------------------------------------------------------------------------
# Claiming a fresh instance
# ---------------------------------------------------------------------------

_setup_code: str | None = None


async def setup_needed(sess: AsyncSession, environ: dict[str, str] | None = None) -> bool:
    """Whether nobody can manage access: no owner named and no live grant that may."""
    if owner_emails(environ):
        return False
    grants = (await sess.scalars(select(Grant).where(live()))).all()
    return not any(ACCESS_MANAGE in effective(g.profile, g.allow, g.deny) for g in grants)


def setup_code() -> str:
    """The one-time code for this process, made on first use and written to the log."""
    global _setup_code
    if _setup_code is None:
        _setup_code = secrets.token_hex(4).upper()
        log.warning(
            "nobody can manage access yet; claim it in the web app with this code",
            extra={"setup_code": _setup_code, "fix": "or set MERIDIAN_OWNER_EMAILS"},
        )
    return _setup_code


def forget_setup_code() -> None:
    """Drop the code; for tests, and after a claim."""
    global _setup_code
    _setup_code = None


async def claim(sess: AsyncSession, subject: str, code: str) -> Grant:
    """Grant ``subject`` operator access with people management, by the setup code.

    Flushes, does not commit. Refuses once anyone can manage access, and a wrong code.
    """
    if not await setup_needed(sess):
        raise GrantRefused("this instance already has someone who manages access")
    expected = setup_code()
    if not hmac.compare_digest(code.strip().upper(), expected):
        raise GrantRefused("that is not the code in the server's log")
    key = subject.strip().lower()
    until = dt.datetime.now(dt.UTC) + dt.timedelta(days=SETUP_GRANT_DAYS)
    existing = await sess.scalar(select(Grant).where(Grant.subject == key, live()))
    if existing is not None:
        grant = await update_grant(
            sess,
            existing,
            actor=SETUP_ACTOR,
            profile="operator",
            allow=sorted({*existing.allow, ACCESS_MANAGE}),
            deny=[p for p in existing.deny if p != ACCESS_MANAGE],
            expires_at=max(until, existing.expires_at or until),
        )
    else:
        grant = await create_grant(
            sess,
            subject=key,
            actor=SETUP_ACTOR,
            profile="operator",
            allow=[ACCESS_MANAGE],
            expires_at=until,
            note="claimed with the setup code",
        )
    forget_setup_code()
    return grant
