"""Temporal client helpers shared by the API and the worker."""

from __future__ import annotations

import logging

from temporalio.client import Client

from nightjar.api.settings import get_settings
from nightjar.worker.workflow import IncidentWorkflow, IncidentWorkflowInput

log = logging.getLogger(__name__)

_client: Client | None = None


async def get_temporal_client() -> Client:
    global _client
    if _client is None:
        settings = get_settings()
        _client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        log.info(
            "connected to temporal %s/%s",
            settings.temporal_address,
            settings.temporal_namespace,
        )
    return _client


def workflow_id(incident_id: str) -> str:
    return f"incident:{incident_id}"


async def start_incident_workflow(
    incident_id: str,
    *,
    approval_ttl_seconds: int,
    stability_seconds: int,
) -> str:
    """Start the durable workflow for a new or retried incident."""
    settings = get_settings()
    client = await get_temporal_client()
    identifier = workflow_id(incident_id)
    await client.start_workflow(
        IncidentWorkflow.run,
        IncidentWorkflowInput(
            incident_id=incident_id,
            approval_ttl_seconds=approval_ttl_seconds,
            stability_seconds=stability_seconds,
        ),
        id=identifier,
        task_queue=settings.temporal_task_queue,
    )
    return identifier


async def signal_approval(incident_id: str, approved: bool) -> None:
    """Wake a workflow waiting on an operator approval decision."""
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id(incident_id))
    await handle.signal(IncidentWorkflow.approval_decided, approved)


__all__ = [
    "get_temporal_client",
    "signal_approval",
    "start_incident_workflow",
    "workflow_id",
]
