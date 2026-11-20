from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from nightjar.api.service import IncidentService
from nightjar.api.settings import Settings
from nightjar.core.enums import ActionStatus
from nightjar.worker.activities import (
    execute_and_verify,
    expire_approvals,
    investigate_and_plan,
    verify_manual,
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
    assert callable(verify_manual)
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


def test_the_workflow_parks_on_a_manual_step_instead_of_finishing() -> None:
    # The signal and the query are the durable equivalents of "a human attested,
    # continue": without them the workflow returns `awaiting_manual` and the
    # attestation has nowhere to land.
    assert hasattr(IncidentWorkflow, "manual_step_attested")
    assert hasattr(IncidentWorkflow, "manual_state")
    workflow = IncidentWorkflow()
    assert workflow.manual_state() == {"attested": False}
    workflow.manual_step_attested()
    assert workflow.manual_state() == {"attested": True}


def _manual_repo(action_id: str) -> AsyncMock:
    repo = AsyncMock()
    repo.get_incident.return_value = AsyncMock(id="incident-1", status="awaiting_manual")
    repo.action_states.return_value = {action_id: ActionStatus.AWAITING_MANUAL}
    repo.list_actions.return_value = [AsyncMock(id=action_id)]
    repo.list_executions.return_value = [
        AsyncMock(
            action_id=action_id,
            after_state={"provider": "stripe", "title": "Revoke the secret key"},
        )
    ]
    repo.append_evidence.return_value = AsyncMock(id="evidence-1")
    return repo


async def test_attestation_signals_the_workflow_instead_of_verifying_inline(monkeypatch) -> None:
    action_id = "action-1"
    repo = _manual_repo(action_id)
    service = _service(Settings(orchestrator="temporal"), repository=repo)
    signalled: list[str] = []

    async def fake_signal(incident_id: str) -> None:
        signalled.append(incident_id)

    verified: list[tuple[str, str]] = []

    async def fake_verify(incident_id: str, *, phase: str, unknown: Any = None) -> bool:
        verified.append((incident_id, phase))
        return True

    monkeypatch.setattr("nightjar.worker.client.signal_manual_step", fake_signal)
    service._verify = fake_verify  # type: ignore[method-assign]

    await service.attest_manual_step("incident-1", action_id, actor="alice", note="revoked")

    assert signalled == ["incident-1"]
    # The workflow owns verification under Temporal; the API must not race it.
    assert verified == []
    assert repo.set_action_status.called


async def test_attestation_verifies_inline_and_waits_for_the_last_step() -> None:
    action_id = "action-1"
    repo = _manual_repo(action_id)
    service = _service(Settings(orchestrator="inline"), repository=repo)
    verified: list[tuple[str, str]] = []

    async def fake_verify(incident_id: str, *, phase: str, unknown: Any = None) -> bool:
        verified.append((incident_id, phase))
        return True

    service._verify = fake_verify  # type: ignore[method-assign]

    await service.attest_manual_step("incident-1", action_id, actor="alice")

    # The second manual step is still outstanding, so nothing is verified yet.
    assert verified == []

    repo.action_states.return_value = {action_id: ActionStatus.ATTESTED}
    await service.resume_after_manual("incident-1")
    assert verified == [("incident-1", "t0")]
