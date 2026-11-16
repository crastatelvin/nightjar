"""Signed alert intake.

This is the only mutating endpoint that does not take a bearer token. Its
credential is the shared HMAC secret: the request must carry a timestamp inside
the tolerance window and a signature over the exact bytes received. The endpoint
is disabled entirely when no secret is configured, so a deployment cannot
accidentally expose an unauthenticated way to file an incident.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from nightjar.api import schemas
from nightjar.api.deps import get_service
from nightjar.api.service import IncidentService
from nightjar.core.enums import IncidentSource
from nightjar.core.signatures import SignatureError, verify_signature

router = APIRouter(prefix="/v1/intake", tags=["intake"])


def _body_limit(request: Request) -> int:
    return int(request.app.state.settings.intake_max_body_bytes)


def _too_large(limit: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"webhook body exceeds {limit} bytes",
    )


async def _read_body(request: Request, limit: int) -> bytes:
    """Read the body, stopping as soon as it exceeds the cap.

    A declared length is checked first so an oversized request is rejected
    without reading it. A chunked request is read incrementally and abandoned at
    the limit instead of being buffered whole.
    """
    raw = request.headers.get("content-length")
    if raw is not None:
        try:
            declared = int(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="content-length is not an integer",
            ) from exc
        if declared > limit:
            raise _too_large(limit)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise _too_large(limit)
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/webhook",
    response_model=schemas.IntakeAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def intake_webhook(
    request: Request,
    service: Annotated[IncidentService, Depends(get_service)],
) -> schemas.IntakeAccepted:
    """Accept a signed alert and file an incident.

    Headers::

        x-nightjar-signature: t=<unix>,v1=<hex hmac-sha256 of "<t>.<body>">
        x-nightjar-delivery: <sender id, optional>

    The delivery id is only used to reject replays. When the sender does not
    provide one, the SHA-256 of the body is used, which still rejects a replay of
    the exact captured request.
    """
    settings = request.app.state.settings
    secret = settings.intake_webhook_secret
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "inbound webhook intake is disabled; set "
                "NIGHTJAR_INTAKE_WEBHOOK_SECRET to enable it"
            ),
        )

    limit = _body_limit(request)
    body = await _read_body(request, limit)

    try:
        verify_signature(
            secret=secret,
            header=request.headers.get("x-nightjar-signature", ""),
            body=body,
            timestamp_header=request.headers.get("x-nightjar-timestamp"),
            tolerance_seconds=settings.intake_webhook_tolerance_seconds,
        )
    except SignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Nightjar-Signature"},
        ) from exc

    try:
        decoded: Any = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook body is not valid JSON",
        ) from exc
    if not isinstance(decoded, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook body must be a JSON object",
        )
    try:
        payload = schemas.WebhookIntake.model_validate(decoded)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors(include_url=False),
        ) from exc

    delivery_id = (
        request.headers.get("x-nightjar-delivery", "").strip() or hashlib.sha256(body).hexdigest()
    )
    first_delivery = await request.app.state.intake_replay.claim(delivery_id)
    if not first_delivery:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"delivery {delivery_id!r} was already accepted",
        )

    incident, created = await service.intake_event(
        title=payload.title,
        description=payload.description,
        incident_class=payload.incident_class,
        severity=payload.severity,
        source=IncidentSource.WEBHOOK,
        metadata=payload.metadata,
        dedupe_key=payload.dedupe_key or f"webhook:{delivery_id}",
    )
    return schemas.IntakeAccepted(
        incident_id=incident.id,
        delivery_id=delivery_id,
        deduplicated=not created,
    )


__all__ = ["router"]
