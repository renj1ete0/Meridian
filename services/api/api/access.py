"""Cloudflare Access assertions (task P3-08, spec §11.4, §12.6).

Verifies `Cf-Access-Jwt-Assertion` against the team's keys and audience on every request;
the header itself is never trusted, and one that does not verify is refused. Not installed
without a team domain. `/health` bypasses it.
See docs/features/api-and-access.md#identity.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Awaitable, Callable

import jwt
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from meridian_core.logging import get_logger

log = get_logger(__name__)

#: Cloudflare's header. Cased as they send it; matched case-insensitively.
ASSERTION_HEADER = "Cf-Access-Jwt-Assertion"

#: Paths served without an assertion. Deliberately a small, explicit set rather
#: than a prefix match — a prefix exempts everything added under it later, and
#: nobody revisits the exemption when they add a route.
UNPROTECTED = frozenset({"/health"})

ALGORITHMS = ["RS256"]


@dataclasses.dataclass(frozen=True)
class AccessSettings:
    """Deployment, not code (§11.11)."""

    team_domain: str
    audience: str

    @property
    def issuer(self) -> str:
        return f"https://{self.team_domain}"

    @property
    def jwks_url(self) -> str:
        return f"https://{self.team_domain}/cdn-cgi/access/certs"

    @classmethod
    def from_env(cls) -> AccessSettings | None:
        """Both or neither.

        A domain without an audience would accept any Access application on the team.
        """
        team = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip()
        audience = os.environ.get("CF_ACCESS_AUD", "").strip()
        if not (team and audience):
            return None
        return cls(team_domain=team, audience=audience)


class AccessVerifier:
    """Verifies assertions against the team's published keys."""

    def __init__(self, settings: AccessSettings, *, jwk_client: object | None = None) -> None:
        self._settings = settings
        # Injectable for tests. The real client caches and refreshes as keys rotate.
        self._keys = jwk_client or jwt.PyJWKClient(settings.jwks_url, cache_keys=True)

    def verify(self, assertion: str) -> dict[str, object] | None:
        """The claims, or None. Never raises to the caller.

        None for every failure, undistinguished, as for token rejections (`P3-03`).
        """
        try:
            key = self._keys.get_signing_key_from_jwt(assertion)
            return jwt.decode(
                assertion,
                key.key,
                algorithms=ALGORITHMS,
                audience=self._settings.audience,
                issuer=self._settings.issuer,
                # Every one of these is on by default and named anyway, because
                # this is the list a future reader will want to see stated
                # rather than inferred from a library's defaults.
                options={"require": ["exp", "iat", "aud", "iss"], "verify_exp": True},
            )
        except jwt.PyJWTError as exc:
            log.warning("access assertion refused", extra={"reason": type(exc).__name__})
            return None
        except Exception as exc:  # pragma: no cover - key fetch failures
            # A JWKS endpoint that is unreachable must not become a bypass. It
            # is an outage, and an outage refuses requests.
            log.error("access key material unavailable", extra={"reason": type(exc).__name__})
            return None


def access_middleware(verifier: AccessVerifier) -> Callable:
    """Refuse anything without a verifiable assertion."""

    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in UNPROTECTED:
            return await call_next(request)

        assertion = request.headers.get(ASSERTION_HEADER)
        if not assertion:
            return JSONResponse(
                status_code=401,
                content={"detail": "No Cloudflare Access assertion on this request."},
            )

        claims = verifier.verify(assertion)
        if claims is None:
            return JSONResponse(
                status_code=401, content={"detail": "Access assertion did not verify."}
            )

        # The identity, for the request's own logging and for `P3-06`'s grants
        # to resolve against later. Read from verified claims, never the header.
        request.state.access_subject = claims.get("email") or claims.get("sub")
        return await call_next(request)

    return guard
