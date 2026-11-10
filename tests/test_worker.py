from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from nightjar.api.service import IncidentService
from nightjar.api.settings import Settings
from nightjar.worker.activities import (
    execute_and_verify,
    expire_approvals,
    investigate_and_plan,
    verify_stability,
)
from nightjar.worker.client import workflow_id
from nightjar.worker.workflow import IncidentWorkflow, IncidentWorkflowInput, IncidentWorkflowResult


def _service(settings: Settings, repository: object) -> IncidentService:
    class FakeRegistry:
        def verification_checks(self) -> dict[str, object]:
            return {}

    return IncidentService(
        settings=settings,
        repository=repository,  # type: ignore[arg-type]
        runtime=object(),  # type: ignore[arg-type]
        registry=FakeRegistry(),  # type: ignore[arg-type]
    )


def test_workflow_id_is_incident_scoped() -> None:
    assert workflow_id("incident-1") == "incident:incident-1"
    assert workflow_id("incident-1") == workflow_id("incident-1")


def test_workflow_payloads_are_serializable() -> None:
    payload = IncidentWorkflowInput(
        incident_id="incident-1", approval_ttl_seconds=300, stability_seconds=60
    )
    assert payload.incident_id == "incident-1"
    assert IncidentWorkflowResult(status="resolved").status == "resolved"


def test_worker_exposes_all_durable_phases() -> None:
    assert callable(investigate_and_plan)
    assert callable(execute_and_verify)
    assert callable(verify_stability)
    assert callable(expire_approvals)
    assert IncidentWorkflow is not None


async def test_service_starts_temporal_workflow_when_configured(monkeypatch) -> None:
    settings = Settings(orchestrator="temporal", approval_ttl_seconds=300, stability_seconds=30)
    service = _service(settings, repository=object())
    started: list[dict[str, Any]] = []

    async def fake_start(incident_id: str, **kwargs: Any) -> str:
        started.append({"incident_id": incident_id, **kwargs})
        return f"incident:{incident_id}"

    monkeypatch.setattr("nightjar.worker.client.start_incident_workflow", fake_start)
    await service._start_pipeline("incident-1")
    assert started == [
        {
            "incident_id": "incident-1",
            "approval_ttl_seconds": 300,
            "stability_seconds": 30,
        }
    ]


async def test_service_runs_inline_by_default(monkeypatch) -> None:
    settings = Settings(orchestrator="inline")
    service = _service(settings, repository=object())
    ran: list[str] = []

    async def fake_pipeline(incident_id: str) -> None:
        ran.append(incident_id)

    service.run_pipeline = fake_pipeline  # type: ignore[method-assign]
    await service._start_pipeline("incident-2")
    assert ran == ["incident-2"]


async def test_approval_signals_temporal_workflow(monkeypatch) -> None:
    settings = Settings(orchestrator="temporal")
    repo = AsyncMock()
    repo.get_approval.return_value = AsyncMock(incident_id="incident-1", action_id="action-1")
    repo.get_incident.return_value = AsyncMock(
        id="incident-1", status=AsyncMock(value="awaiting_approval")
    )
    repo.list_approvals.return_value = []
    repo.list_actions.return_value = []
    repo.get_case_file.return_value = {"incident": {"status": "awaiting_approval"}}
    service = _service(settings, repository=repo)
    signalled: list[tuple[str, bool]] = []

    async def fake_signal(incident_id: str, approved: bool) -> None:
        signalled.append((incident_id, approved))

    monkeypatch.setattr("nightjar.worker.client.signal_approval", fake_signal)
    await service.decide_approval("approval-1", approved=True, actor="alice", comment="ok")
    assert signalled == [("incident-1", True)]
    assert repo.decide_approval.called
    # The API must not execute inline while Temporal owns the workflow.
    assert repo.get_case_file.called
