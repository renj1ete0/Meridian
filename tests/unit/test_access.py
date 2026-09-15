"""Cloudflare Access assertions (task P3-08, spec §11.4, §12.6).

No database and no network: a local RSA key signs the assertions and stands in
for the team's published key set, so every branch is reachable without a
Cloudflare account.

**The header is the attack.** An application that reads an identity out of
`Cf-Access-Jwt-Assertion` without verifying it is one misconfiguration from
letting anyone be anyone — a direct port publish, a second ingress, a proxy
added later for an unrelated reason. None of those looks like a security change
when it is made, which is why the verification has to be unconditional rather
than a belt-and-braces extra.
"""

from __future__ import annotations

import datetime as dt

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from api.access import ASSERTION_HEADER, UNPROTECTED, AccessSettings, AccessVerifier

TEAM = "example-team.cloudflareaccess.com"
AUD = "aud-tag-for-this-application"

SETTINGS = AccessSettings(team_domain=TEAM, audience=AUD)


@pytest.fixture(scope="module")
def keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_keypair():
    """A different, perfectly valid key. The impersonation attempt."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def sign(key, **claims) -> str:
    now = dt.datetime.now(dt.UTC)
    payload = {
        "aud": AUD,
        "iss": f"https://{TEAM}",
        "iat": now,
        "exp": now + dt.timedelta(minutes=10),
        "email": "reader@example.test",
        "sub": "user-1",
        **claims,
    }
    return jwt.encode(payload, key, algorithm="RS256")


class _Keys:
    """Stands in for `PyJWKClient`, serving one key regardless of `kid`."""

    def __init__(self, key) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str):
        class _Signing:
            key = self._key.public_key()

        return _Signing()


@pytest.fixture
def verifier(keypair):
    return AccessVerifier(SETTINGS, jwk_client=_Keys(keypair))


# --------------------------------------------------------------------------
# A genuine assertion
# --------------------------------------------------------------------------


def test_a_valid_assertion_yields_its_claims(verifier, keypair) -> None:
    claims = verifier.verify(sign(keypair))

    assert claims is not None
    assert claims["email"] == "reader@example.test"


# --------------------------------------------------------------------------
# Every way one must be refused
# --------------------------------------------------------------------------


def test_an_assertion_signed_by_another_key_is_refused(verifier, other_keypair) -> None:
    """The attack this exists for. The token is well-formed, unexpired, carries
    the right audience and issuer, and says whatever the attacker wants — and is
    signed by a key the team never published."""
    assert verifier.verify(sign(other_keypair)) is None


def test_an_unsigned_assertion_is_refused(verifier) -> None:
    """`alg: none`. The oldest JWT attack there is, and it works against any
    verifier that takes the algorithm from the token instead of naming it."""
    forged = jwt.encode(
        {"aud": AUD, "iss": f"https://{TEAM}", "email": "a@b.test"}, None, algorithm="none"
    )

    assert verifier.verify(forged) is None


def test_an_assertion_for_another_application_is_refused(verifier, keypair) -> None:
    """Signed by the right team, for a different Access application.

    This is why `from_env` demands an audience as well as a team domain: without
    it, any application on the same team would authenticate for this one.
    """
    assert verifier.verify(sign(keypair, aud="a-different-application")) is None


def test_an_assertion_from_another_team_is_refused(verifier, keypair) -> None:
    assert verifier.verify(sign(keypair, iss="https://someone-else.cloudflareaccess.com")) is None


def test_an_expired_assertion_is_refused(verifier, keypair) -> None:
    past = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    assert verifier.verify(sign(keypair, exp=past)) is None


def test_an_assertion_with_no_expiry_is_refused(verifier, keypair) -> None:
    """A token that never expires is a credential nobody can withdraw. `exp` is
    in the required list rather than merely checked-if-present."""
    now = dt.datetime.now(dt.UTC)
    token = jwt.encode(
        {"aud": AUD, "iss": f"https://{TEAM}", "iat": now, "email": "a@b.test"},
        keypair,
        algorithm="RS256",
    )
    assert verifier.verify(token) is None


@pytest.mark.parametrize("garbage", ["", "not-a-jwt", "a.b.c", "Bearer something"])
def test_malformed_assertions_are_refused(verifier, garbage: str) -> None:
    assert verifier.verify(garbage) is None


def test_unavailable_key_material_refuses_rather_than_bypasses(keypair) -> None:
    """A JWKS endpoint that is down is an outage, and an outage refuses.

    The tempting failure here is to let requests through when the keys cannot be
    fetched, so that a Cloudflare problem does not take the service down. That
    converts a dependency outage into an authentication bypass, and it would do
    so at exactly the moment nobody is watching.
    """

    class _Broken:
        def get_signing_key_from_jwt(self, token):
            raise RuntimeError("jwks unreachable")

    assert AccessVerifier(SETTINGS, jwk_client=_Broken()).verify(sign(keypair)) is None


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_both_settings_are_required(monkeypatch) -> None:
    """A team domain alone would verify that *some* application on the team
    signed this, which is not the same as authenticating for this one."""
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)
    assert AccessSettings.from_env() is None

    monkeypatch.setenv("CF_ACCESS_AUD", AUD)
    assert AccessSettings.from_env() is not None


def test_the_unprotected_set_is_small_and_explicit() -> None:
    """A prefix would exempt every route added under it later, and nobody
    revisits an exemption when adding a route. `/health` is exempt because a
    watchdog cannot complete an SSO flow, and it discloses nothing about the
    corpus by design."""
    assert {"/health"} == UNPROTECTED


def test_the_header_name_is_cloudflares() -> None:
    assert ASSERTION_HEADER.lower() == "cf-access-jwt-assertion"


# --------------------------------------------------------------------------
# The middleware, wired
# --------------------------------------------------------------------------


@pytest.fixture
def client(keypair):
    """A minimal app behind the guard, so the middleware is the only subject."""
    from api.access import access_middleware

    app = FastAPI()
    app.middleware("http")(access_middleware(AccessVerifier(SETTINGS, jwk_client=_Keys(keypair))))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/explore/stats")
    async def stats() -> dict[str, int]:
        return {"sources": 0}

    return TestClient(app)


def test_a_request_with_no_assertion_is_refused(client) -> None:
    assert client.get("/api/explore/stats").status_code == 401


def test_a_request_with_a_forged_assertion_is_refused(client, other_keypair) -> None:
    response = client.get("/api/explore/stats", headers={ASSERTION_HEADER: sign(other_keypair)})
    assert response.status_code == 401


def test_a_verified_request_reaches_the_route(client, keypair) -> None:
    response = client.get("/api/explore/stats", headers={ASSERTION_HEADER: sign(keypair)})

    assert response.status_code == 200
    assert response.json() == {"sources": 0}


def test_health_answers_without_an_assertion(client) -> None:
    """The watchdog cannot complete an SSO flow, and a service that cannot be
    probed gets restarted by the thing probing it."""
    assert client.get("/health").status_code == 200


def test_the_identity_comes_from_the_claims_not_the_headers(keypair) -> None:
    """A caller who also sends `Cf-Access-Authenticated-User-Email` — which
    Cloudflare really does send, unsigned — must not have it believed. The
    subject is read from the verified payload and nowhere else.
    """
    from api.access import access_middleware

    app = FastAPI()
    app.middleware("http")(access_middleware(AccessVerifier(SETTINGS, jwk_client=_Keys(keypair))))

    @app.get("/who")
    async def who(request: Request) -> dict[str, str]:
        return {"subject": request.state.access_subject}

    response = TestClient(app).get(
        "/who",
        headers={
            ASSERTION_HEADER: sign(keypair, email="real@example.test"),
            "Cf-Access-Authenticated-User-Email": "attacker@evil.test",
        },
    )

    assert response.json() == {"subject": "real@example.test"}
