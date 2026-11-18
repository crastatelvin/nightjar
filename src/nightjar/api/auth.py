"""Authentication: one shared token, or OIDC when more than one person has access.

Two credential styles, deliberately different in what they can promise:

* **Shared token** (`NIGHTJAR_API_TOKEN`). One secret for the whole deployment.
  Everyone who holds it can do everything, so the actor name that arrives in
  ``x-nightjar-actor`` is an audit label, not an authorization decision. This is
  the right shape for a one-person operations team and it is honest about it.
* **OIDC** (`NIGHTJAR_OIDC_ISSUER` + `NIGHTJAR_OIDC_AUDIENCE`). The actor comes
  from a verified token and ``x-nightjar-actor`` is ignored, because a caller
  who has a real identity must not be able to claim to be someone else. This is
  the shape that makes separation of duties mean anything.

A deployment that configures only an issuer must not fall open. That is the bug
this module exists to prevent: an issuer with no verifier is an open API, and
`Settings` now refuses that combination outright.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import jwt

DISCOVERY_TTL_SECONDS = 3600.0
# Asymmetric algorithms only. HS256 is excluded on purpose: accepting a symmetric
# algorithm here would let a caller sign its own token with the public JWKS.
ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384")
CLOCK_SKEW_SECONDS = 30


class AuthError(ValueError):
    """A credential was not accepted. Never includes the credential itself."""


class AuthRequired(AuthError):
    """No credential was supplied at all."""


class AuthRejected(AuthError):
    """A credential was supplied and refused."""


@dataclass(frozen=True)
class Identity:
    """Who made the request, according to the credential that was presented."""

    subject: str
    method: Literal["token", "oidc"]
    email: str = ""
    groups: tuple[str, ...] = ()

    @property
    def actor(self) -> str:
        """The name recorded in the timeline and on approvals."""
        return (self.email or self.subject)[:100]

    def in_group(self, names: Collection[str]) -> bool:
        """Whether this identity carries any of ``names`` in its group claim.

        Group values are compared exactly to what the issuer put in the claim;
        a directory that emits object ids rather than names is configured with
        those ids, because guessing a mapping would be inventing a claim.
        """
        return any(group in names for group in self.groups)


def _bearer(authorization: str | None) -> str:
    if not authorization:
        raise AuthRequired("bearer token required")
    scheme, _, supplied = authorization.partition(" ")
    if scheme.lower() != "bearer" or not supplied.strip():
        raise AuthRequired("bearer token required")
    return supplied.strip()


class OIDCVerifier:
    """Verify bearer JWTs against an issuer's published keys.

    Discovery and JWKS are cached for an hour, and an unknown ``kid`` triggers one
    refetch so a key rotation does not lock everyone out. Nothing here trusts an
    unverified claim: the algorithm allowlist, the issuer, the audience, and the
    expiry are all checked before a single claim is read.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        groups_claim: str = "groups",
        timeout: float = 10.0,
        discovery_ttl: float = DISCOVERY_TTL_SECONDS,
    ) -> None:
        if not issuer:
            raise ValueError("OIDC verifier needs an issuer")
        if not audience:
            raise ValueError("OIDC verifier needs an audience")
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._groups_claim = groups_claim
        self._timeout = timeout
        self._ttl = discovery_ttl
        self._jwks: jwt.PyJWKSet | None = None
        self._fetched_at = 0.0

    async def verify(self, token: str) -> Identity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthRejected("token is not a JWT") from exc
        algorithm = str(header.get("alg", ""))
        if algorithm not in ALLOWED_ALGORITHMS:
            raise AuthRejected(f"token algorithm {algorithm or 'missing'} is not accepted")
        key = await self._key(str(header.get("kid", "")))
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[algorithm],
                audience=self._audience,
                issuer=self._issuer,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthRejected(f"token rejected: {exc}") from exc
        return Identity(
            subject=str(claims["sub"]),
            method="oidc",
            email=str(claims.get("email") or claims.get("preferred_username") or ""),
            groups=_groups(claims.get(self._groups_claim)),
        )

    # ------------------------------------------------------------------ jwks
    async def _key(self, kid: str) -> Any:
        jwks = await self._jwks_set()
        jwk = _find(jwks, kid)
        if jwk is None:
            # One refetch covers a rotation; a second miss is a real rejection.
            jwk = _find(await self._jwks_set(force=True), kid)
        if jwk is None:
            raise AuthRejected("token signing key is not published by the issuer")
        return jwk.key

    async def _jwks_set(self, *, force: bool = False) -> jwt.PyJWKSet:
        fresh = self._jwks is not None and (time.monotonic() - self._fetched_at) < self._ttl
        if fresh and not force:
            assert self._jwks is not None
            return self._jwks
        document = await self._discovery()
        uri = str(document.get("jwks_uri", ""))
        if not uri:
            raise AuthRejected("the issuer's discovery document has no jwks_uri")
        if not uri.startswith(_scheme(self._issuer)):
            raise AuthRejected(
                "the issuer's jwks_uri does not use the issuer's scheme; refusing a downgrade"
            )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(uri)
        except httpx.HTTPError as exc:
            raise AuthRejected(f"could not reach the issuer's keys: {exc}") from exc
        if response.status_code >= 400:
            raise AuthRejected(f"the issuer's jwks_uri returned {response.status_code}")
        try:
            self._jwks = jwt.PyJWKSet.from_dict(response.json())
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise AuthRejected("the issuer published an unusable key set") from exc
        self._fetched_at = time.monotonic()
        return self._jwks

    async def _discovery(self) -> dict[str, Any]:
        url = f"{self._issuer}/.well-known/openid-configuration"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise AuthRejected(f"could not reach the OIDC issuer: {exc}") from exc
        if response.status_code >= 400:
            raise AuthRejected(f"the OIDC discovery endpoint returned {response.status_code}")
        try:
            document = response.json()
        except ValueError as exc:
            raise AuthRejected("the OIDC discovery document is not JSON") from exc
        if not isinstance(document, dict):
            raise AuthRejected("the OIDC discovery document is not an object")
        published = str(document.get("issuer", "")).rstrip("/")
        if published != self._issuer:
            raise AuthRejected(
                f"the discovery document is for {published or 'no issuer'}, not {self._issuer}"
            )
        return document


async def authenticate(
    authorization: str | None,
    *,
    token: str | None,
    verifier: OIDCVerifier | None,
) -> Identity | None:
    """Return the caller's identity, or ``None`` when authentication is off.

    ``None`` is only possible when no credential is configured at all, which the
    production validator refuses.
    """
    if not token and verifier is None:
        return None
    supplied = _bearer(authorization)
    if token and secrets.compare_digest(supplied, token):
        return Identity(subject="shared-token", method="token")
    if verifier is not None:
        return await verifier.verify(supplied)
    raise AuthRejected("invalid token")


def _find(jwks: jwt.PyJWKSet, kid: str) -> jwt.PyJWK | None:
    try:
        if kid:
            return jwks[kid]
        return jwks.keys[0] if len(jwks.keys) == 1 else None
    except KeyError:
        return None


def _groups(value: Any) -> tuple[str, ...]:
    """Accept the list form and the space-separated string form."""
    if isinstance(value, str):
        return tuple(part for part in value.replace(",", " ").split() if part)
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


def _scheme(url: str) -> str:
    return "https://" if url.startswith("https://") else "http://"


__all__ = [
    "ALLOWED_ALGORITHMS",
    "AuthError",
    "AuthRejected",
    "AuthRequired",
    "Identity",
    "OIDCVerifier",
    "authenticate",
]
