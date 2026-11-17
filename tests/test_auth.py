"""Authentication: shared token, OIDC, and the identity the audit trail records.

The verifier tests mint real RS256 tokens and serve a real discovery document and
JWKS, so the algorithm allowlist, issuer, audience, expiry, and key rotation are
exercised rather than asserted. The wiring tests use a stub verifier so the route
behaviour can be checked without a token authority.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from httpx import Response
from jwt.algorithms import RSAAlgorithm

from nightjar.api.auth import (
    AuthRejected,
    AuthRequired,
    Identity,
    OIDCVerifier,
    authenticate,
)
from nightjar.api.settings import Settings

ISSUER = "https://id.example.com"
AUDIENCE = "nightjar"
KID = "key-2026-11"
DISCOVERY = f"{ISSUER}/.well-known/openid-configuration"
JWKS = f"{ISSUER}/jwks"


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwk(private_key: rsa.RSAPrivateKey, kid: str = KID) -> dict[str, Any]:
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return jwk


def token(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str = KID,
    algorithm: str = "RS256",
    **overrides: Any,
) -> str:
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "auth0|alice",
        "email": "alice@acme.example",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "groups": ["nightjar-operators"],
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm=algorithm, headers={"kid": kid})


def discovery(*, issuer: str = ISSUER, jwks_uri: str = JWKS) -> Response:
    return Response(200, json={"issuer": issuer, "jwks_uri": jwks_uri})


def jwks(*keys: dict[str, Any]) -> Response:
    return Response(200, json={"keys": list(keys)})


def verifier(**overrides: Any) -> OIDCVerifier:
    settings: dict[str, Any] = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "groups_claim": "groups",
    }
    settings.update(overrides)
    return OIDCVerifier(**settings)


# ------------------------------------------------------------------- verifier
@respx.mock
async def test_a_valid_token_yields_the_verified_identity(
    private_key: rsa.RSAPrivateKey,
) -> None:
    respx.get(DISCOVERY).mock(return_value=discovery())
    respx.get(JWKS).mock(return_value=jwks(public_jwk(private_key)))

    identity = await verifier().verify(token(private_key))

    assert identity.subject == "auth0|alice"
    assert identity.email == "alice@acme.example"
    assert identity.actor == "alice@acme.example"
    assert identity.groups == ("nightjar-operators",)
    assert identity.method == "oidc"


@respx.mock
async def test_the_keys_are_cached_between_requests(private_key: rsa.RSAPrivateKey) -> None:
    respx.get(DISCOVERY).mock(return_value=discovery())
    keys = respx.get(JWKS).mock(return_value=jwks(public_jwk(private_key)))
    auth = verifier()

    await auth.verify(token(private_key))
    await auth.verify(token(private_key))

    assert keys.call_count == 1


@respx.mock
async def test_a_rotated_key_is_fetched_once(private_key: rsa.RSAPrivateKey) -> None:
    respx.get(DISCOVERY).mock(return_value=discovery())
    keys = respx.get(JWKS).mock(
        side_effect=[
            jwks(public_jwk(private_key, "old")),
            jwks(public_jwk(private_key, "new")),
        ]
    )
    auth = verifier()

    identity = await auth.verify(token(private_key, kid="new"))

    assert identity.actor == "alice@acme.example"
    assert keys.call_count == 2


@respx.mock
async def test_an_unpublished_key_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    respx.get(DISCOVERY).mock(return_value=discovery())
    respx.get(JWKS).mock(return_value=jwks(public_jwk(private_key, "other")))

    with pytest.raises(AuthRejected, match="not published"):
        await verifier().verify(token(private_key))


@respx.mock
async def test_a_token_for_another_audience_is_rejected(
    private_key: rsa.RSAPrivateKey,
) -> None:
    respx.get(DISCOVERY).mock(return_value=discovery())
    respx.get(JWKS).mock(return_value=jwks(public_jwk(private_key)))

    with pytest.raises(AuthRejected, match="Audience"):
        await verifier().verify(token(private_key, aud="another-app"))


@respx.mock
async def test_a_token_from_another_issuer_is_rejected(
    private_key: rsa.RSAPrivateKey,
) -> None:
    respx.get(DISCOVERY).mock(return_value=discovery())
    respx.get(JWKS).mock(return_value=jwks(public_jwk(private_key)))

    with pytest.raises(AuthRejected, match="issuer"):
        await verifier().verify(token(private_key, iss="https://evil.example.com"))


@respx.mock
async def test_an_expired_token_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    respx.get(DISCOVERY).mock(return_value=discovery())
    respx.get(JWKS).mock(return_value=jwks(public_jwk(private_key)))

    expired = token(private_key, iat=int(time.time()) - 3600, exp=int(time.time()) - 1800)
    with pytest.raises(AuthRejected, match="expired"):
        await verifier().verify(expired)


@respx.mock
async def test_a_symmetric_algorithm_is_never_accepted(
    private_key: rsa.RSAPrivateKey,
) -> None:
    """Accepting HS256 would let a caller sign its own token with the public JWKS."""
    respx.get(DISCOVERY).mock(return_value=discovery())
    respx.get(JWKS).mock(return_value=jwks(public_jwk(private_key)))
    forged = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "iat": int(time.time()), "exp": 9_999_999_999},
        "a-public-key-is-not-a-secret-value-at-all",
        algorithm="HS256",
        headers={"kid": KID},
    )

    with pytest.raises(AuthRejected, match="algorithm"):
        await verifier().verify(forged)


@respx.mock
async def test_a_token_without_a_subject_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    respx.get(DISCOVERY).mock(return_value=discovery())
    respx.get(JWKS).mock(return_value=jwks(public_jwk(private_key)))
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    unsigned_subject = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": KID})

    with pytest.raises(AuthRejected):
        await verifier().verify(unsigned_subject)


@respx.mock
async def test_a_discovery_document_for_another_issuer_is_rejected(
    private_key: rsa.RSAPrivateKey,
) -> None:
    respx.get(DISCOVERY).mock(
        return_value=discovery(issuer="https://evil.example.com", jwks_uri=JWKS)
    )
    respx.get(JWKS).mock(return_value=jwks(public_jwk(private_key)))

    with pytest.raises(AuthRejected, match="discovery document"):
        await verifier().verify(token(private_key))


@respx.mock
async def test_a_downgraded_jwks_uri_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    respx.get(DISCOVERY).mock(return_value=discovery(jwks_uri="http://id.example.com/jwks"))

    with pytest.raises(AuthRejected, match="downgrade"):
        await verifier().verify(token(private_key))


@respx.mock
async def test_a_space_separated_groups_claim_is_parsed(
    private_key: rsa.RSAPrivateKey,
) -> None:
    respx.get(DISCOVERY).mock(return_value=discovery())
    respx.get(JWKS).mock(return_value=jwks(public_jwk(private_key)))

    identity = await verifier().verify(
        token(private_key, groups="nightjar-operators nightjar-approvers")
    )

    assert identity.groups == ("nightjar-operators", "nightjar-approvers")


def test_a_verifier_needs_an_issuer_and_an_audience() -> None:
    with pytest.raises(ValueError, match="issuer"):
        OIDCVerifier(issuer="", audience=AUDIENCE)
    with pytest.raises(ValueError, match="audience"):
        OIDCVerifier(issuer=ISSUER, audience="")


# ---------------------------------------------------------------- authenticate
async def test_no_configured_credential_leaves_auth_off() -> None:
    assert await authenticate(None, token=None, verifier=None) is None


async def test_a_missing_credential_is_auth_required() -> None:
    with pytest.raises(AuthRequired):
        await authenticate(None, token="shared", verifier=None)


async def test_a_wrong_shared_token_is_rejected() -> None:
    with pytest.raises(AuthRejected, match="invalid token"):
        await authenticate("Bearer nope", token="shared", verifier=None)


async def test_the_shared_token_is_accepted_without_a_verifier() -> None:
    identity = await authenticate("Bearer shared", token="shared", verifier=None)

    assert identity is not None
    assert identity.method == "token"


# ------------------------------------------------------------------- wiring
class StubVerifier:
    """Stands in for a token authority in route-level tests."""

    def __init__(self, identity: Identity, *, accepted: str = "good-token") -> None:
        self.identity = identity
        self.accepted = accepted

    async def verify(self, token: str) -> Identity:
        if token != self.accepted:
            raise AuthRejected("token rejected: bad signature")
        return self.identity


@pytest.fixture
def oidc_client(client: TestClient) -> Iterator[TestClient]:
    settings = client.app.state.settings
    original = settings.api_token
    settings.api_token = None
    client.app.state.oidc = StubVerifier(
        Identity(
            subject="auth0|alice",
            method="oidc",
            email="alice@acme.example",
            groups=("nightjar-operators",),
        )
    )
    try:
        yield client
    finally:
        settings.api_token = original
        client.app.state.oidc = None


def test_oidc_rejects_a_request_with_no_token(oidc_client: TestClient) -> None:
    response = oidc_client.get("/v1/incidents")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_oidc_rejects_a_token_the_issuer_did_not_sign(oidc_client: TestClient) -> None:
    response = oidc_client.get("/v1/incidents", headers={"authorization": "Bearer forged"})

    assert response.status_code == 403
    assert "rejected" in response.json()["detail"]


def test_oidc_accepts_a_verified_token(oidc_client: TestClient) -> None:
    response = oidc_client.get("/v1/incidents", headers={"authorization": "Bearer good-token"})

    assert response.status_code == 200


def test_the_oidc_identity_is_the_recorded_reporter(oidc_client: TestClient) -> None:
    """The actor header must not be able to claim to be someone else."""
    response = oidc_client.post(
        "/v1/incidents",
        json={"title": "Checkout is returning 502", "incident_class": "site_outage"},
        headers={"authorization": "Bearer good-token", "x-nightjar-actor": "mallory"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["reported_by"] == "alice@acme.example"


def test_the_shared_token_mode_keeps_the_self_asserted_actor(client: TestClient) -> None:
    """One shared credential has no per-user identity; the header is the audit label."""
    settings = client.app.state.settings
    original = settings.api_token
    settings.api_token = "shared-token"
    try:
        response = client.post(
            "/v1/incidents",
            json={"title": "Checkout is returning 502", "incident_class": "site_outage"},
            headers={"authorization": "Bearer shared-token", "x-nightjar-actor": "dana"},
        )
    finally:
        settings.api_token = original

    assert response.status_code == 201, response.text
    assert response.json()["reported_by"] == "dana"


def test_development_with_no_credential_is_open(client: TestClient) -> None:
    assert client.app.state.settings.api_token is None
    assert client.get("/v1/incidents").status_code == 200


def test_a_production_settings_object_cannot_boot_with_an_unverifiable_issuer() -> None:
    with pytest.raises(Exception, match="NIGHTJAR_OIDC_AUDIENCE"):
        Settings(
            _env_file=None,
            environment="production",
            lab_enabled=False,
            allow_private_probes=False,
            oidc_issuer=ISSUER,
            managed_targets={"cloudflare": {"zones": ["zone-1"]}},
            stability_seconds=900,
        )
