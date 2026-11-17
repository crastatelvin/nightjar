from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from nightjar.api import schemas
from nightjar.api.deps import actor, get_service, require_auth
from nightjar.api.repository import CaseFile, ConflictError, NotFoundError
from nightjar.api.service import IncidentService
from nightjar.core.enums import ActionStatus, IncidentStatus
from nightjar.core.ids import utcnow
from nightjar.core.models import Incident

router = APIRouter(
    prefix="/v1/incidents",
    tags=["incidents"],
    dependencies=[Depends(require_auth)],
)


def _manual_steps(case: CaseFile) -> list[schemas.ManualStepView]:
    executions = {
        execution.action_id: execution
        for execution in case.executions
        if execution.after_state.get("provider")
    }
    steps: list[schemas.ManualStepView] = []
    for action in case.actions:
        state = case.action_states.get(action.id)
        execution = executions.get(action.id)
        if execution is None or state not in {
            ActionStatus.AWAITING_MANUAL,
            ActionStatus.ATTESTED,
        }:
            continue
        manual = execution.after_state
        steps.append(
            schemas.ManualStepView(
                action_id=action.id,
                action_type=action.action_type.value,
                target=action.target,
                description=action.description,
                risk=action.risk.value,
                state=state,
                provider=str(manual.get("provider", "")),
                title=str(manual.get("title", "")),
                instructions=[str(item) for item in manual.get("instructions", [])],
                dashboard_url=str(manual.get("dashboard_url", "")),
                fingerprint=str(manual.get("fingerprint", "")),
                probe_url=str(manual.get("probe_url", "")),
                auth=str(manual.get("auth", "bearer")),
            )
        )
    return steps


def _case_response(
    case: CaseFile,
    *,
    evidence_valid: bool,
    timeline_valid: bool,
    checked_evidence: int,
    checked_timeline: int,
) -> schemas.CaseFileResponse:
    return schemas.CaseFileResponse(
        incident=case.incident,
        evidence=case.evidence,
        timeline=case.timeline,
        plan=case.plan,
        actions=case.actions,
        action_states=case.action_states,
        manual_steps=_manual_steps(case),
        approvals=case.approvals,
        verifications=case.verifications,
        executions=case.executions,
        agent_runs=case.agent_runs,
        notes=case.notes,
        findings=case.findings,
        chain=schemas.ChainStatus(
            evidence_valid=evidence_valid,
            timeline_valid=timeline_valid,
            evidence_checked=checked_evidence,
            timeline_checked=checked_timeline,
        ),
    )


async def _load_case(service: IncidentService, incident_id: str) -> schemas.CaseFileResponse:
    try:
        case = await service.repo.get_case_file(incident_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    evidence_chain, timeline_chain = await service.repo.verify_chains(incident_id)
    return _case_response(
        case,
        evidence_valid=evidence_chain.valid,
        timeline_valid=timeline_chain.valid,
        checked_evidence=evidence_chain.checked,
        checked_timeline=timeline_chain.checked,
    )


@router.post("", response_model=Incident, status_code=201)
async def create_incident(
    payload: schemas.IncidentCreate,
    service: Annotated[IncidentService, Depends(get_service)],
    reported_by: Annotated[str, Depends(actor)],
    wait: bool = Query(default=False, description="Run the pipeline before responding"),
) -> Incident:
    incident = await service.intake(
        title=payload.title,
        description=payload.description,
        incident_class=payload.incident_class,
        severity=payload.severity,
        source=payload.source,
        metadata=payload.metadata,
        dedupe_key=payload.dedupe_key,
        reported_by=reported_by,
        wait=wait,
    )
    return Incident.model_validate(incident.model_dump())


@router.get("", response_model=schemas.IncidentList)
async def list_incidents(
    service: Annotated[IncidentService, Depends(get_service)],
    status_filter: IncidentStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> schemas.IncidentList:
    incidents, total = await service.repo.list_incidents(
        service.settings.tenant_id, status=status_filter, limit=limit, offset=offset
    )
    return schemas.IncidentList(
        items=[schemas.IncidentListItem.model_validate(item.model_dump()) for item in incidents],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{incident_id}", response_model=schemas.CaseFileResponse)
async def get_incident(
    incident_id: str,
    service: Annotated[IncidentService, Depends(get_service)],
) -> schemas.CaseFileResponse:
    return await _load_case(service, incident_id)


@router.get(
    "/{incident_id}/manual-steps",
    response_model=list[schemas.ManualStepView],
)
async def list_manual_steps(
    incident_id: str,
    service: Annotated[IncidentService, Depends(get_service)],
) -> list[schemas.ManualStepView]:
    case = await _load_case(service, incident_id)
    return case.manual_steps


@router.post(
    "/{incident_id}/manual-steps/{action_id}/attest",
    response_model=schemas.CaseFileResponse,
)
async def attest_manual_step(
    incident_id: str,
    action_id: str,
    payload: schemas.ManualStepAttest,
    service: Annotated[IncidentService, Depends(get_service)],
    attested_by: Annotated[str, Depends(actor)],
) -> schemas.CaseFileResponse:
    try:
        await service.attest_manual_step(
            incident_id,
            action_id,
            actor=attested_by,
            note=payload.note,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _load_case(service, incident_id)


@router.get("/{incident_id}/timeline", response_model=schemas.TimelineList)
async def get_timeline(
    incident_id: str,
    service: Annotated[IncidentService, Depends(get_service)],
    after_seq: int = Query(default=0, ge=0),
) -> schemas.TimelineList:
    await _ensure_incident(service, incident_id)
    events = await service.repo.list_timeline(incident_id, after_seq=after_seq)
    return schemas.TimelineList(events=events)


@router.get("/{incident_id}/chain", response_model=schemas.ChainStatus)
async def get_chain(
    incident_id: str,
    service: Annotated[IncidentService, Depends(get_service)],
) -> schemas.ChainStatus:
    await _ensure_incident(service, incident_id)
    evidence, timeline = await service.repo.verify_chains(incident_id)
    return schemas.ChainStatus(
        evidence_valid=evidence.valid,
        timeline_valid=timeline.valid,
        evidence_checked=evidence.checked,
        timeline_checked=timeline.checked,
        broken_at=evidence.broken_at or timeline.broken_at,
    )


@router.get("/{incident_id}/stream")
async def stream_timeline(
    incident_id: str,
    request: Request,
    service: Annotated[IncidentService, Depends(get_service)],
    after_seq: int = Query(default=0, ge=0),
) -> StreamingResponse:
    await _ensure_incident(service, incident_id)

    async def generate() -> AsyncIterator[str]:
        last = after_seq
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            events = await service.repo.list_timeline(incident_id, after_seq=last)
            if events:
                idle = 0
                for event in events:
                    last = event.seq
                    payload = json.dumps(event.model_dump(mode="json"), default=str)
                    yield f"id: {event.seq}\nevent: timeline\ndata: {payload}\n\n"
            else:
                idle += 1
                if idle >= 10:
                    idle = 0
                    yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache",
            "connection": "keep-alive",
            "x-accel-buffering": "no",
        },
    )


@router.post("/{incident_id}/notes", response_model=schemas.CaseFileResponse)
async def add_note(
    incident_id: str,
    payload: schemas.NoteCreate,
    service: Annotated[IncidentService, Depends(get_service)],
    author: Annotated[str, Depends(actor)],
) -> schemas.CaseFileResponse:
    await _ensure_incident(service, incident_id)
    await service.repo.add_note(incident_id, author, payload.body)
    return await _load_case(service, incident_id)


@router.post("/{incident_id}/retry", response_model=Incident)
async def retry_incident(
    incident_id: str,
    service: Annotated[IncidentService, Depends(get_service)],
    wait: bool = Query(default=False),
) -> Incident:
    try:
        incident = await service.retry(incident_id, wait=wait)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Incident.model_validate(incident.model_dump())


@router.post("/{incident_id}/verify", response_model=schemas.CaseFileResponse)
async def verify_now(
    incident_id: str,
    service: Annotated[IncidentService, Depends(get_service)],
) -> schemas.CaseFileResponse:
    """Bring the scheduled stability check forward (used by demos and evals)."""
    await _ensure_incident(service, incident_id)
    incident = await service.repo.get_incident(incident_id)
    if incident.status is not IncidentStatus.VERIFYING:
        raise HTTPException(
            status_code=409,
            detail=f"incident is {incident.status.value}, not verifying",
        )
    await service.repo.set_next_check(incident_id, utcnow())
    return await _load_case(service, incident_id)


async def _ensure_incident(service: IncidentService, incident_id: str) -> None:
    try:
        await service.repo.get_incident(incident_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["router"]
