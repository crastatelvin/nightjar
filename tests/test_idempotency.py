"""Idempotency: one key per mutation, and a replay that does not call the provider.

The second test is the one that matters. Re-running the executor on the same plan
is exactly what a duplicate approval decision or a retried worker does, and the
connector-call counter is the witness: the provider is never touched twice.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from nightjar.api import db as database
from nightjar.api.main import create_app
from nightjar.api.settings import Settings
from nightjar.connectors.idempotency import action_key, canonical_params
from nightjar.core.enums import ActionStatus, ActionType
from nightjar.core.models import ActionSpec
from tests.support import scenario_payload


def action(*, action_id: str = "act-1", **params: Any) -> ActionSpec:
    return ActionSpec(
        id=action_id,
        action_type=ActionType.PURGE_CACHE,
        target="zone-1",
        params=params,
    )


def test_the_key_is_stable_for_the_same_mutation() -> None:
    first = action_key("incident-1", "plan-1", action(urls=["https://a.example"]))
    second = action_key("incident-1", "plan-1", action(urls=["https://a.example"]))

    assert first == second
    assert len(first) == 64


def test_parameter_order_does_not_change_the_key() -> None:
    assert canonical_params({"b": 1, "a": 2}) == canonical_params({"a": 2, "b": 1})
    assert action_key("i", "p", action(b=1, a=2)) == action_key("i", "p", action(a=2, b=1))


def test_a_different_parameter_changes_the_key() -> None:
    assert action_key("i", "p", action(urls=["a"])) != action_key("i", "p", action(urls=["b"]))


def test_a_different_action_changes_the_key() -> None:
    assert action_key("i", "p", action(action_id="a")) != action_key(
        "i", "p", action(action_id="b")
    )


def test_a_new_plan_may_act_again() -> None:
    """A reopened incident gets a new plan, and the fix has to be re-applied."""
    same_action = action(urls=["https://a.example"])

    assert action_key("incident-1", "plan-1", same_action) != action_key(
        "incident-1", "plan-2", same_action
    )


def test_a_different_incident_gets_a_different_key() -> None:
    same_action = action(urls=["https://a.example"])

    assert action_key("incident-1", "plan-1", same_action) != action_key(
        "incident-2", "plan-1", same_action
    )


def test_a_planner_pinned_key_is_part_of_the_material() -> None:
    pinned = action(urls=["https://a.example"]).model_copy(update={"idempotency_key": "pinned"})

    assert action_key("i", "p", pinned) != action_key("i", "p", action(urls=["https://a.example"]))


def test_a_second_execution_of_the_same_plan_does_not_touch_the_provider(
    settings: Settings, lab: httpx.Client
) -> None:
    payload = scenario_payload(lab, "site_cache_poisoned")
    app = create_app(settings)
    service = app.state.service
    real_execute = service.registry.execute
    calls = {"count": 0}

    async def counted(spec: ActionSpec) -> dict[str, Any]:
        calls["count"] += 1
        return await real_execute(spec)

    service.registry.execute = counted  # type: ignore[method-assign]

    async def scenario() -> tuple[int, int, list[dict[str, Any]], str]:
        try:
            # The lifespan does this; this test drives the service directly so
            # the replay happens in the same event loop as the original run.
            await service.repo.ensure_tenant(settings.tenant_id, settings.tenant_name)
            incident = await service.intake(**payload, wait=True)
            case = await service.repo.get_case_file(incident.id)
            assert case.incident.status.value == "resolved"
            first_pass = calls["count"]
            assert first_pass >= 1

            action_id = case.actions[0].id
            # The state a duplicate approval decision or a retried worker sees.
            await service.repo.set_action_status(action_id, ActionStatus.APPROVED)
            await service._execute_and_verify(incident.id)

            after = await service.repo.get_case_file(incident.id)
            replays = [
                event.model_dump(mode="json")
                for event in after.timeline
                if event.event_type.value == "action.replayed"
            ]
            return first_pass, calls["count"], replays, after.incident.status.value
        finally:
            await service.shutdown()
            await database.dispose()

    first_pass, second_pass, replays, status = asyncio.run(scenario())

    assert second_pass == first_pass
    assert status == "resolved"
    assert replays
    assert replays[0]["payload"]["replay_of"]
    assert replays[0]["payload"]["result"]
