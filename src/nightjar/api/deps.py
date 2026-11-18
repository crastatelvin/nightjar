from __future__ import annotations

from fastapi import Header, HTTPException, Request, status

from nightjar.api import ratelimit
from nightjar.api.auth import AuthRejected, AuthRequired, Identity, OIDCVerifier, authenticate
from nightjar.api.service import IncidentService


def _spend_budget(request: Request, key: str) -> None:
    """Charge one request to ``key`` and refuse when the budget is gone.

    The decision is left on the request so the response can carry the remaining
    budget; the middleware turns that into ``x-ratelimit-*`` headers. A ``None``
    limiter means the deployment turned the budget off.
    """
    limiter: ratelimit.CallerLimiter | None = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return
    decision = limiter.check(key)
    request.state.rate_limit = decision
    if decision.allowed:
        return
    retry = ratelimit.retry_after_header(decision)
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(f"rate limit exceeded ({decision.limit} requests per minute); retry in {retry}s"),
        headers={
            "Retry-After": retry,
            "x-ratelimit-limit": str(decision.limit),
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset": retry,
        },
    )


def _peer_key(request: Request) -> str:
    settings = request.app.state.settings
    address = ratelimit.peer_address(
        request.headers,
        request.client.host if request.client else None,
        trust_forwarded_for=settings.api_trust_forwarded_for,
    )
    return f"peer:{address}"


def enforce_peer_budget(request: Request) -> None:
    """Charge the budget to the caller's address.

    This runs before any credential is checked. Billing only successful requests
    would make a flood of bad tokens free, which is the cheapest denial of
    service there is; a refused credential costs a token like any other request.
    The address is the only key available at that point, and with a trusted
    proxy setting ``X-Forwarded-For`` it is the real client's address.
    """
    _spend_budget(request, _peer_key(request))


async def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Authenticate the request and remember who made it.

    With no credential configured this is a no-op for local development, which the
    production validator refuses. Otherwise a missing credential is ``401`` and a
    refused one is ``403``, so a client can tell "log in" from "you are not allowed".
    """
    enforce_peer_budget(request)
    settings = request.app.state.settings
    verifier: OIDCVerifier | None = getattr(request.app.state, "oidc", None)
    try:
        identity = await authenticate(authorization, token=settings.api_token, verifier=verifier)
    except AuthRequired as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except AuthRejected as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    request.state.identity = identity


async def actor(
    request: Request,
    x_nightjar_actor: str = Header(default="operator"),
) -> str:
    """The name written to the timeline, notes, and approvals.

    With OIDC the name comes from the verified token and the header is ignored:
    a caller with a real identity must not be able to claim to be someone else.
    With the shared token there is no per-user identity to verify, so the header
    is the audit label and everyone holding the token is equally trusted.
    """
    identity: Identity | None = getattr(request.state, "identity", None)
    if identity is not None and identity.method == "oidc":
        return identity.actor
    return (x_nightjar_actor or "operator").strip()[:100]


def get_service(request: Request) -> IncidentService:
    service: IncidentService = request.app.state.service
    return service
