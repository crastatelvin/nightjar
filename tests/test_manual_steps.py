from __future__ import annotations

from typing import Any, cast

import pytest

from nightjar.agents.mock import MockAgentRuntime
from nightjar.api.repository import ConflictError
from nightjar.api.service import IncidentService
from nightjar.api.settings import Settings
from nightjar.connectors.registry import ConnectorRegistry
from nightjar.connectors.stripe import StripeConnector
from nightjar.core.enums import (
    ActionStatus,
    ActionType,
    EventType,
    EvidenceKind,
    IncidentClass,
    IncidentStatus,
    PredicateKind,
    RiskClass,
)
from nightjar.core.ids import new_id
from nightjar.core.models import (
    ActionExecution,
    ActionPlan,
    ActionSpec,
    EvidenceItem,
    Incident,
    VerificationPredicate,
    VerificationResult,
)

KEY = "sk_live_51NExampleSecretKey"


class FakeRepo:
    """Only the repository surface ``_execute_and_verify`` touches."""

    def __init__(self, incident: Incident, plan: ActionPlan, actions: list[ActionSpec]) -> None:
        self.incident = incident
        self.plan = plan
        self.actions = actions
        self.states = {action.id: ActionStatus.APPROVED for action in actions}
        self.events: list[tuple[EventType, dict[str, Any]]] = []
        self.executions: dict[str, ActionExecution] = {}
        self.evidence: list[EvidenceItem] = []
        self.verifications: list[VerificationResult] = []
        self.reopens = 0
        self.next_check: Any = None

    async def get_incident(self, incident_id: str) -> Incident:
        return self.incident

    async def latest_plan(self, incident_id: str) -> ActionPlan:
        return self.plan

    async def list_actions(self, incident_id: str) -> list[ActionSpec]:
        return self.actions

    async def action_states(self, incident_id: str) -> dict[str, ActionStatus]:
        return dict(self.states)

    async def set_action_status(self, action_id: str, status: ActionStatus) -> None:
        self.states[action_id] = status

    async def record_execution_start(
        self, incident_id: str, action: ActionSpec, before_state: dict[str, Any]
    ) -> ActionExecution:
        execution = ActionExecution(
            incident_id=incident_id,
            action_id=action.id,
            status=ActionStatus.EXECUTING,
            before_state=before_state,
        )
        self.executions[execution.id] = execution
        return execution

    async def record_execution_finish(
        self,
        execution_id: str,
        *,
        status: ActionStatus,
        after_state: dict[str, Any],
        error: str | None = None,
    ) -> ActionExecution:
        execution = self.executions[execution_id].model_copy(
            update={"status": status, "after_state": after_state, "error": error}
        )
        self.executions[execution_id] = execution
        return execution

    async def append_event(
        self,
        incident_id: str,
        event_type: EventType,
        *,
        actor: str,
        payload: dict[str, Any],
    ) -> Any:
        self.events.append((event_type, payload))

    async def update_incident(self, incident_id: str, **fields: Any) -> Incident:
        self.incident = self.incident.model_copy(update=fields)
        return self.incident

    async def list_executions(self, incident_id: str) -> list[ActionExecution]:
        return list(self.executions.values())

    async def append_evidence(self, item: EvidenceItem) -> EvidenceItem:
        self.evidence.append(item)
        return item

    async def record_verification(
        self, result: VerificationResult, *, phase: str, required: bool
    ) -> VerificationResult:
        self.verifications.append(result)
        return result

    async def set_next_check(self, incident_id: str, when: Any) -> None:
        self.next_check = when

    async def increment_reopen(self, incident_id: str) -> None:
        self.reopens += 1

    async def ensure_tenant(self, tenant_id: str, name: str) -> None:
        return None

    async def due_for_verification(self, now: Any) -> list[str]:
        return []

    async def get_case_file(self, incident_id: str) -> Any:
        from nightjar.api.repository import CaseFile

        return CaseFile(
            incident=self.incident,
            evidence=self.evidence,
            timeline=[],
            plan=self.plan,
            actions=self.actions,
            action_states=dict(self.states),
            executions=list(self.executions.values()),
            verifications=self.verifications,
        )

    async def verify_chains(self, incident_id: str) -> Any:
        from nightjar.core.models import ChainVerification

        return (
            ChainVerification(valid=True, checked=0),
            ChainVerification(valid=True, checked=0),
        )


def manual_plan() -> tuple[Incident, ActionPlan]:
    incident = Incident(
        tenant_id="tenant-manual",
        title="Exposed Stripe key",
        incident_class=IncidentClass.CREDENTIAL_EXPOSURE,
        status=IncidentStatus.EXECUTING,
    )
    predicate = VerificationPredicate(
        id=new_id(),
        kind=PredicateKind.CREDENTIAL,
        description="The exposed Stripe key returns 401",
        params={
            "credential_ref": "env:NIGHTJAR_TEST_STRIPE_KEY",
            "probe_url": "https://api.stripe.com/v1/balance",
            "auth": "basic",
        },
    )
    action = ActionSpec(
        action_type=ActionType.REVOKE_CREDENTIAL,
        target="cred-1",
        params={
            "connector": "stripe",
            "credential_id": "cred-1",
            "credential_ref": "env:NIGHTJAR_TEST_STRIPE_KEY",
        },
        risk=RiskClass.R2,
        description="Expire the exposed Stripe key in the Dashboard.",
        rollback="Expiring a key is irreversible; rotate and audit usage.",
        predicate_ids=[predicate.id],
    )
    plan = ActionPlan(
        summary="Expire the key",
        root_cause="The key was committed to a public repository.",
        confidence=0.9,
        actions=[action],
        predicates=[predicate],
        blast_radius=["credential:cred-1"],
        rollback_plan="Rotate and audit.",
    )
    return incident, plan


async def test_a_manual_action_pauses_the_incident_without_executing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NIGHTJAR_TEST_STRIPE_KEY", KEY)
    incident, plan = manual_plan()
    repo = FakeRepo(incident, plan, plan.actions)
    service = IncidentService(
        settings=Settings(_env_file=None),
        repository=cast("Any", repo),
        runtime=MockAgentRuntime(),
        registry=ConnectorRegistry([StripeConnector(None)]),
    )

    await service._execute_and_verify(incident.id)

    action = plan.actions[0]
    assert repo.states[action.id] is ActionStatus.AWAITING_MANUAL
    assert repo.incident.status is IncidentStatus.AWAITING_MANUAL

    execution = next(iter(repo.executions.values()))
    assert execution.status is ActionStatus.AWAITING_MANUAL
    assert execution.after_state["provider"] == "stripe"
    assert execution.after_state["auth"] == "basic"
    assert execution.after_state["instructions"]

    event_types = [event_type for event_type, _ in repo.events]
    assert EventType.MANUAL_STEP_REQUIRED in event_types
    required = next(
        payload for kind, payload in repo.events if kind is EventType.MANUAL_STEP_REQUIRED
    )
    assert required["manual"]["dashboard_url"] == "https://dashboard.stripe.com/apikeys"
    assert KEY not in str(required)


class FakeVerifier:
    def __init__(self, passed: bool) -> None:
        self.passed = passed

    async def run(
        self,
        predicate: VerificationPredicate,
        *,
        incident_id: str,
        action_id: str | None,
    ) -> VerificationResult:
        return VerificationResult(
            incident_id=incident_id,
            action_id=action_id,
            predicate_id=predicate.id,
            kind=predicate.kind,
            passed=self.passed,
            observed={"status": 401 if self.passed else 403},
            phase="t0",
            required=True,
        )


def build_service(
    monkeypatch: pytest.MonkeyPatch, *, passed: bool
) -> tuple[IncidentService, FakeRepo]:
    monkeypatch.setenv("NIGHTJAR_TEST_STRIPE_KEY", KEY)
    incident, plan = manual_plan()
    repo = FakeRepo(incident, plan, plan.actions)
    service = IncidentService(
        settings=Settings(_env_file=None, stability_seconds=0),
        repository=cast("Any", repo),
        runtime=MockAgentRuntime(),
        registry=ConnectorRegistry([StripeConnector(None)]),
    )
    service.verifier = FakeVerifier(passed)  # type: ignore[assignment]
    return service, repo


async def test_attestation_records_evidence_and_resolves_only_when_the_probe_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repo = build_service(monkeypatch, passed=True)
    action = repo.actions[0]
    await service._execute_and_verify(repo.incident.id)

    await service.attest_manual_step(
        repo.incident.id, action.id, actor="alice", note="expired in the Dashboard"
    )

    assert repo.states[action.id] is ActionStatus.ATTESTED
    assert repo.incident.status is IncidentStatus.RESOLVED
    assert repo.verifications[-1].passed is True
    note = next(item for item in repo.evidence if item.kind is EvidenceKind.HUMAN_NOTE)
    assert note.collected_by == "alice"
    assert note.content["note"] == "expired in the Dashboard"
    assert note.content["fingerprint"]
    event_types = [event_type for event_type, _ in repo.events]
    assert EventType.MANUAL_STEP_ATTESTED in event_types


async def test_a_false_attestation_reopens_the_incident_and_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repo = build_service(monkeypatch, passed=False)
    action = repo.actions[0]
    await service._execute_and_verify(repo.incident.id)

    await service.attest_manual_step(repo.incident.id, action.id, actor="alice")

    assert repo.incident.status is IncidentStatus.REOPENED
    assert repo.reopens == 1
    assert repo.verifications[-1].passed is False

    # The operator really expires the key and attests again.
    service.verifier = FakeVerifier(True)  # type: ignore[assignment]
    await service.attest_manual_step(repo.incident.id, action.id, actor="alice")

    assert repo.incident.status is IncidentStatus.RESOLVED
    assert repo.verifications[-1].passed is True


async def test_an_automated_action_cannot_be_attested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repo = build_service(monkeypatch, passed=True)
    action = repo.actions[0]

    with pytest.raises(ConflictError, match="not awaiting a manual step"):
        await service.attest_manual_step(repo.incident.id, action.id, actor="alice")


async def test_the_case_file_exposes_the_manual_step_and_its_proof_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nightjar.api.repository import CaseFile
    from nightjar.api.routers.incidents import _manual_steps

    service, repo = build_service(monkeypatch, passed=True)
    await service._execute_and_verify(repo.incident.id)
    case = CaseFile(
        incident=repo.incident,
        actions=repo.actions,
        action_states=repo.states,
        executions=list(repo.executions.values()),
    )

    steps = _manual_steps(case)

    assert len(steps) == 1
    assert steps[0].provider == "stripe"
    assert steps[0].state is ActionStatus.AWAITING_MANUAL
    assert steps[0].auth == "basic"
    assert steps[0].probe_url.endswith("/v1/balance")
    assert steps[0].instructions
    assert KEY not in steps[0].model_dump_json()


def test_the_manual_step_api_lists_the_step_and_attests_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from fastapi.testclient import TestClient

    from nightjar.api.main import create_app

    service, repo = build_service(monkeypatch, passed=True)
    action = repo.actions[0]
    asyncio.run(service._execute_and_verify(repo.incident.id))
    app = create_app(Settings(_env_file=None, stability_seconds=0), service=service)

    with TestClient(app) as client:
        listed = client.get(f"/v1/incidents/{repo.incident.id}/manual-steps")
        assert listed.status_code == 200
        steps = listed.json()
        assert len(steps) == 1
        assert steps[0]["provider"] == "stripe"
        assert steps[0]["state"] == "awaiting_manual"
        assert steps[0]["instructions"]

        attested = client.post(
            f"/v1/incidents/{repo.incident.id}/manual-steps/{action.id}/attest",
            json={"note": "expired in the Dashboard"},
            headers={"x-nightjar-actor": "alice"},
        )

    assert attested.status_code == 200
    body = attested.json()
    assert body["incident"]["status"] == "resolved"
    assert body["manual_steps"][0]["state"] == "attested"
    assert any(item["kind"] == "human_note" for item in body["evidence"])


def test_a_busy_incident_returns_409_with_retry_after(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """Another replica owning the incident must not be waited on or raced."""
    import asyncio

    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from nightjar.api.locks import incident_lock
    from nightjar.api.main import create_app

    service, repo = build_service(monkeypatch, passed=True)
    engine = create_async_engine(database_url, poolclass=NullPool)
    service._lock_engine = engine
    action = repo.actions[0]
    asyncio.run(service._execute_and_verify(repo.incident.id))
    app = create_app(Settings(_env_file=None, stability_seconds=0, db_locks=False), service=service)

    async def scenario() -> Any:
        try:
            async with incident_lock(engine, repo.incident.id):
                with TestClient(app) as client:
                    return client.post(
                        f"/v1/incidents/{repo.incident.id}/manual-steps/{action.id}/attest",
                        json={"note": "expired in the Dashboard"},
                    )
        finally:
            await engine.dispose()

    response = asyncio.run(scenario())

    assert response.status_code == 409
    assert response.headers["retry-after"] == "2"
    assert "another Nightjar process" in response.json()["detail"]
