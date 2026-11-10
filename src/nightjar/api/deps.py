from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request, status

from nightjar.api.service import IncidentService


async def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    token = request.app.state.settings.api_token
    if not token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    supplied = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(supplied, token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid token")


async def actor(
    x_nightjar_actor: str = Header(default="operator"),
) -> str:
    return (x_nightjar_actor or "operator").strip()[:100]


def get_service(request: Request) -> IncidentService:
    service: IncidentService = request.app.state.service
    return service
