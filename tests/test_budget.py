from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nightjar.agents.budget import Budget, BudgetExceeded
from nightjar.agents.mock import MockAgentRuntime
from nightjar.agents.types import AgentOutcome
from nightjar.api.main import build_service, create_app
from nightjar.api.settings import Settings
from tests.support import scenario_payload, submit, wait_for_status


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_cost_is_charged_after_the_call_and_stops_the_run() -> None:
    budget = Budget(max_cost_usd=0.10, max_seconds=60, max_calls=8)
    budget.charge("triage", 0.04)
    budget.charge("investigator", 0.04)

    with pytest.raises(BudgetExceeded) as excinfo:
        budget.charge("critic", 0.04)

    assert excinfo.value.limit == "cost"
    assert excinfo.value.ceiling == 0.10
    assert budget.cost_usd == pytest.approx(0.12)
    assert budget.calls == 3
    # The stop is sticky: the next role is refused before it starts.
    with pytest.raises(BudgetExceeded) as next_role:
        budget.ensure_can_start("planner")
    assert next_role.value.limit == "cost"


def test_a_spent_cost_budget_refuses_the_next_role() -> None:
    budget = Budget(max_cost_usd=0.10, max_seconds=60, max_calls=8)
    with pytest.raises(BudgetExceeded):
        budget.charge("triage", 0.11)
    with pytest.raises(BudgetExceeded):
        budget.ensure_can_start("investigator")


def test_wall_clock_ceiling_refuses_the_next_role() -> None:
    clock = FakeClock()
    budget = Budget(max_cost_usd=1.0, max_seconds=10, max_calls=8, clock=clock)
    clock.now = 10.0

    with pytest.raises(BudgetExceeded) as excinfo:
        budget.ensure_can_start("triage")

    assert excinfo.value.limit == "seconds"
    assert excinfo.value.observed == 10.0
    assert budget.remaining_seconds == 0.0


def test_remaining_time_is_clamped_at_zero() -> None:
    clock = FakeClock()
    budget = Budget(max_cost_usd=1.0, max_seconds=10, max_calls=8, clock=clock)
    clock.now = 4.0
    assert budget.remaining_seconds == 6.0
    clock.now = 25.0
    assert budget.remaining_seconds == 0.0


def test_call_ceiling_bounds_a_provider_that_reports_no_cost() -> None:
    budget = Budget(max_cost_usd=1.0, max_seconds=60, max_calls=2)
    budget.charge("triage", 0.0)
    budget.charge("investigator", 0.0)

    with pytest.raises(BudgetExceeded) as excinfo:
        budget.ensure_can_start("critic")

    assert excinfo.value.limit == "calls"
    assert excinfo.value.observed == 2.0


def test_snapshot_reports_both_spend_and_ceilings() -> None:
    clock = FakeClock()
    budget = Budget(max_cost_usd=0.5, max_seconds=30, max_calls=4, clock=clock)
    budget.charge("triage", 0.2)
    clock.now = 12.5

    snapshot = budget.snapshot()
    assert snapshot == {
        "cost_usd": 0.2,
        "calls": 1,
        "elapsed_seconds": 12.5,
        "max_cost_usd": 0.5,
        "max_seconds": 30.0,
        "max_calls": 4,
    }


class ChargingRuntime(MockAgentRuntime):
    """The reference runtime, with one role's usage inflated.

    This is how a real hosted model behaves when a prompt grows: the cost
    arrives with the result, after the provider has already done the work.
    """

    def __init__(self, role: str, cost_usd: float) -> None:
        self.role = role
        self.cost_usd = cost_usd

    def _charge(self, role: str, outcome: AgentOutcome[object]) -> AgentOutcome[object]:
        if role != self.role:
            return outcome
        return replace(outcome, cost_usd=self.cost_usd)

    async def triage(self, payload):  # type: ignore[no-untyped-def]
        return self._charge("triage", await super().triage(payload))

    async def investigate(self, payload):  # type: ignore[no-untyped-def]
        return self._charge("investigator", await super().investigate(payload))

    async def critique(self, payload):  # type: ignore[no-untyped-def]
        return self._charge("critic", await super().critique(payload))

    async def plan(self, payload):  # type: ignore[no-untyped-def]
        return self._charge("planner", await super().plan(payload))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("role", "calls"),
    [("triage", 1), ("investigator", 2), ("critic", 3), ("planner", 4)],
)
def test_a_budget_stop_ends_the_incident_before_any_action(
    settings: Settings, lab, role: str, calls: int
) -> None:
    budgeted = settings.model_copy(update={"max_cost_usd": 0.5})
    service = build_service(budgeted)
    service.runtime = ChargingRuntime(role, cost_usd=0.6)
    app = create_app(budgeted, service=service)

    with TestClient(app) as client:
        payload = scenario_payload(lab, "site_cache_poisoned")
        incident = submit(client, payload)
        case = wait_for_status(client, incident["id"], {"failed"})

    assert "model budget exceeded" in case["incident"]["summary"]
    assert case["plan"] is None
    assert case["actions"] == []

    stops = [
        event for event in case["timeline"] if event["event_type"] == "incident.budget_exceeded"
    ]
    assert len(stops) == 1
    payload = stops[0]["payload"]
    assert payload["limit"] == "cost"
    assert payload["ceiling"] == 0.5
    assert payload["calls"] == calls
    assert payload["cost_usd"] == pytest.approx(0.6)
