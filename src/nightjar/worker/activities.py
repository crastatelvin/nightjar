"""Temporal activities for durable incident orchestration.

Activities are thin, retry-bounded wrappers around the same service methods the
inline orchestrator uses. They never decide policy, verify outcomes, or mutate
incident state beyond what the service already permits.
"""

from __future__ import annotations

from temporalio import activity

from nightjar.api.main import build_service
from nightjar.api.service import IncidentService
from nightjar.api.settings import get_settings
from nightjar.core.enums import ActionStatus, IncidentClass, IncidentStatus

_service: IncidentService | None = None


def get_activity_service() -> IncidentService:
    """Build one service per activity worker process."""
    global _service
    if _service is None:
        _service = build_service(get_settings())
    return _service


@activity.defn
async def investigate_and_plan(incident_id: str) -> str:
    """Triage, gather evidence, critique, plan, and run the policy gate."""
    service = get_activity_service()
    incident = await service.repo.get_incident(incident_id)
    if incident.status not in {IncidentStatus.INTAKE, IncidentStatus.REOPENED}:
        return incident.status.value
    await service._set_status(
        incident, IncidentStatus.INVESTIGATING, "workflow investigation started"
    )
    bundle = await service._investigate(incident)
    if (
        bundle.critique.overall.value == "contradicted"
        or not bundle.findings
        or bundle.triage.incident_class is IncidentClass.UNKNOWN
    ):
        await service._fail(
            incident_id,
            "investigation did not produce a supported, classifiable finding",
        )
        return "failed"
    plan = await service._plan(incident, bundle)
    if plan is None:
        return "failed"
    incident = await service.repo.get_incident(incident_id)
    await service._gate_and_execute(incident, plan, bundle)
    incident = await service.repo.get_incident(incident_id)
    return incident.status.value


@activity.defn
async def execute_and_verify(incident_id: str) -> str:
    """Execute approved actions and run the T+0 verification predicates."""
    service = get_activity_service()
    await service._execute_and_verify(incident_id)
    incident = await service.repo.get_incident(incident_id)
    return incident.status.value


@activity.defn
async def verify_stability(incident_id: str) -> str:
    """Run the required predicates again after the stability window."""
    service = get_activity_service()
    await service._verify(incident_id, phase="stability")
    incident = await service.repo.get_incident(incident_id)
    return incident.status.value


@activity.defn
async def expire_approvals(incident_id: str) -> str:
    """Cancel an unapproved plan when the operator window closes."""
    service = get_activity_service()
    await service.repo.expire_pending_approvals(incident_id)
    states = await service.repo.action_states(incident_id)
    for action in await service.repo.list_actions(incident_id):
        if states.get(action.id) not in {
            ActionStatus.EXECUTED,
            ActionStatus.FAILED,
            ActionStatus.UNKNOWN,
            ActionStatus.DENIED,
        }:
            await service.repo.set_action_status(action.id, ActionStatus.CANCELLED)
    await service._fail(incident_id, "operator approval window expired")
    incident = await service.repo.get_incident(incident_id)
    return incident.status.value


__all__ = [
    "execute_and_verify",
    "expire_approvals",
    "get_activity_service",
    "investigate_and_plan",
    "verify_stability",
]
