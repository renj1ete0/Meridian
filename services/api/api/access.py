"""Cloudflare Access assertions (task P3-08, spec §11.4, §12.6).

When a tunnel fronts this service, Cloudflare authenticates the person and
passes a signed JWT in `Cf-Access-Jwt-Assertion`. This verifies it.

**The header is never trusted.** That is the whole task, and the reason it is a
task rather than a line of code: a header is a string, and an application that
reads the identity out of one is a single misconfiguration away from letting
anyone assert any identity by typing it. A direct port publish, a second
ingress, a reverse proxy added later for an unrelated reason — each of those
turns "Cloudflare always sets this" into "anyone can set this", and none of them
looks like a security change when it is made.

So every assertion is verified cryptographically against the team's published
keys, with the audience checked, on every request. An assertion that does not
verify is refused; there is deliberately no path where an unverifiable one is
treated as anonymous-but-allowed, because that path is the bug.

**Not configured means not installed.** A deployment with no team domain runs
without this middleware and says so at startup — it is for a service behind a
tunnel, and demanding it locally would push everyone into disabling it.
`P3-03`'s token check is the layer that does not depend on deployment shape, and
it fails closed on its own.

**`/health` bypasses**, because the watchdog cannot complete an SSO flow. It
reports liveness and a database connection and deliberately nothing about the
corpus (`P2-07`), so exempting it discloses nothing.
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

        A team domain without an audience would verify that *some* Access
        application signed the token — including one belonging to a different
        service on the same team, which is not the same as authenticating for
        this one.
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
        # Injectable so a test can supply a local key set. The real client
        # caches and refreshes on its own; Cloudflare rotates keys, and a
        # verifier that fetched them once would start failing weeks later for a
        # reason nobody would connect to a deploy.
        self._keys = jwk_client or jwt.PyJWKClient(settings.jwks_url, cache_keys=True)

    def verify(self, assertion: str) -> dict[str, object] | None:
        """The claims, or None. Never raises to the caller.

        None for every failure — bad signature, wrong audience, wrong issuer,
        expired, malformed. They are not distinguished in the response for the
        same reason token rejections are not (`P3-03`): the caller is
        unauthenticated, and which check failed tells it how to get closer.
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
