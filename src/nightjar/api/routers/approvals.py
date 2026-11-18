from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from nightjar.api import schemas
from nightjar.api.deps import actor, get_service, require_approver, require_auth
from nightjar.api.repository import ConflictError, NotFoundError
from nightjar.api.routers.incidents import _load_case
from nightjar.api.service import IncidentService
from nightjar.core.models import ApprovalRequest

router = APIRouter(
    prefix="/v1/incidents",
    tags=["approvals"],
    dependencies=[Depends(require_auth)],
)


@router.get("/{incident_id}/approvals", response_model=list[ApprovalRequest])
async def list_approvals(
    incident_id: str,
    service: Annotated[IncidentService, Depends(get_service)],
) -> list[ApprovalRequest]:
    try:
        await service.repo.get_incident(incident_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await service.repo.list_approvals(incident_id)


@router.post(
    "/{incident_id}/approvals/{approval_id}",
    response_model=schemas.CaseFileResponse,
    dependencies=[Depends(require_approver)],
)
async def decide_approval(
    incident_id: str,
    approval_id: str,
    payload: schemas.ApprovalDecision,
    service: Annotated[IncidentService, Depends(get_service)],
    decided_by: Annotated[str, Depends(actor)],
) -> schemas.CaseFileResponse:
    try:
        approval = await service.repo.get_approval(approval_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if approval.incident_id != incident_id:
        raise HTTPException(status_code=404, detail="approval does not belong to this incident")
    try:
        await service.decide_approval(
            approval_id,
            approved=payload.approved,
            actor=decided_by,
            comment=payload.comment,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _load_case(service, incident_id)


__all__ = ["router"]
