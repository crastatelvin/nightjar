"""An action whose outcome the provider never confirmed.

The case is the one where guessing is worst: the request left, the answer did not
come back, and a `POST` may or may not have created something. Retrying risks a
duplicate; declaring failure risks closing a case that was actually fixed. Nightjar
stops, writes down what it does not know, and lets the verifier read the target.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nightjar.api.main import build_service, create_app
from nightjar.api.settings import Settings
from nightjar.connectors.errors import Transient
from tests.support import scenario_payload, submit, wait_for_status


def _lost_response(service: Any) -> None:
    """Make the next dispatch fail as if the provider's answer vanished."""

    async def execute(action: Any) -> dict[str, Any]:
        raise Transient(
            "lab gateway timed out after accepting the request",
            provider="lab",
            maybe_applied=True,
            status=504,
        )

    service.registry.execute = execute  # type: ignore[method-assign]


def _applied_but_unconfirmed(service: Any) -> None:
    """Apply the change for real, then fail the way a dropped response does."""
    real = service.registry.execute

    async def execute(action: Any) -> dict[str, Any]:
        await real(action)
        raise Transient(
            "lab gateway timed out after the mutation",
            provider="lab",
            maybe_applied=True,
            status=504,
        )

    service.registry.execute = execute  # type: ignore[method-assign]


@pytest.mark.integration
def test_an_unconfirmed_action_is_reconciled_by_verification_not_retried(
    settings: Settings, lab: Any
) -> None:
    service = build_service(settings)
    _lost_response(service)
    app = create_app(settings, service=service)
    with TestClient(app) as client:
        incident = submit(client, scenario_payload(lab, "site_cache_poisoned"))
        case = wait_for_status(client, incident["id"], {"failed"})

    # The action says exactly what is true: unknown, not failed.
    (action,) = case["actions"]
    assert case["action_states"][action["id"]] == "unknown"
    (execution,) = [item for item in case["executions"] if item["action_id"] == action["id"]]
    assert execution["status"] == "unknown"
    assert execution["after_state"]["outcome"] == "unknown"
    assert execution["error"]

    event_types = [event["event_type"] for event in case["timeline"]]
    assert "action.outcome_unknown" in event_types
    assert "action.failed" not in event_types

    # Verification ran anyway, and it is what decided the case.
    assert case["verifications"], "reconciliation must read the target"
    assert {item["phase"] for item in case["verifications"]} == {"reconcile"}
    assert not all(item["passed"] for item in case["verifications"])

    summary = case["incident"]["summary"]
    assert "may or may not have applied" in summary
    assert "check the provider" in summary.lower()

    # No second attempt: a duplicate mutation is exactly the failure this avoids.
    assert len(case["executions"]) == 1
    # And no reopening, which would have produced a fresh plan and re-dispatched.
    assert case["incident"]["status"] == "failed"


@pytest.mark.integration
def test_a_dropped_response_that_still_fixed_the_site_resolves_on_proof(
    settings: Settings, lab: Any
) -> None:
    service = build_service(settings)
    _applied_but_unconfirmed(service)
    app = create_app(settings, service=service)
    with TestClient(app) as client:
        incident = submit(client, scenario_payload(lab, "site_cache_poisoned"))
        case = wait_for_status(client, incident["id"], {"resolved"})

    (action,) = case["actions"]
    assert case["action_states"][action["id"]] == "unknown"
    assert case["incident"]["status"] == "resolved"
    assert all(item["passed"] for item in case["verifications"] if item["required"])
    assert "reconcile" in {item["phase"] for item in case["verifications"]}
    assert lab.get("/lab/audit").json()["healthy"] is True


@pytest.mark.integration
def test_the_executor_does_not_retry_a_mutation_it_cannot_confirm(
    settings: Settings, lab: Any
) -> None:
    calls = {"count": 0}
    service = build_service(settings)

    async def execute(action: Any) -> dict[str, Any]:
        calls["count"] += 1
        raise Transient("connection reset by peer", provider="lab", maybe_applied=True)

    service.registry.execute = execute  # type: ignore[method-assign]
    app = create_app(settings, service=service)
    with TestClient(app) as client:
        incident = submit(client, scenario_payload(lab, "site_cache_poisoned"))
        wait_for_status(client, incident["id"], {"failed"})

    assert calls["count"] == 1
