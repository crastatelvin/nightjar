from __future__ import annotations

from fastapi import Header, HTTPException, Request, status

from nightjar.api.auth import AuthRejected, AuthRequired, Identity, OIDCVerifier, authenticate
from nightjar.api.service import IncidentService


async def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Authenticate the request and remember who made it.

    With no credential configured this is a no-op for local development, which the
    production validator refuses. Otherwise a missing credential is ``401`` and a
    refused one is ``403``, so a client can tell "log in" from "you are not allowed".
    """
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
