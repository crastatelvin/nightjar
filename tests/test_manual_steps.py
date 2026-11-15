from __future__ import annotations

from typing import Any, cast

import pytest

from nightjar.agents.mock import MockAgentRuntime
from nightjar.api.service import IncidentService
from nightjar.api.settings import Settings
from nightjar.connectors.registry import ConnectorRegistry
from nightjar.connectors.stripe import StripeConnector
from nightjar.core.enums import (
    ActionStatus,
    ActionType,
    EventType,
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
    Incident,
    VerificationPredicate,
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

    def record_verification(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a manual step must not verify before a human acts")


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
