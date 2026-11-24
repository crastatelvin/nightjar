"""What the executor does when the provider fails mid-flight.

The unit tests prove the taxonomy and the retry policy in isolation. These drive
the real service and a real incident through the failures an operator cannot
choose: a read timeout, a dropped connection, a 5xx after the request left, and
a provider that rate limits before doing anything.

The distinction under test is the one that matters. A failure that may have
applied a mutation must not be repeated, must not be called a failure of the fix,
and must not let the case close as resolved. A failure that provably did not
apply is safe to repeat. Both outcomes have to leave a valid hash chain.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.support import scenario_payload, submit, wait_for_status

from nightjar.api.main import create_app
from nightjar.api.settings import Settings
from nightjar.connectors.errors import ConnectorError, RateLimited, Transient, transport_error
from nightjar.core.models import ActionSpec

MAYBE_APPLIED: list[tuple[str, Callable[[], ConnectorError]]] = [
    (
        "read timeout",
        lambda: transport_error("lab", httpx.ReadTimeout("read timed out"), "purge cache"),
    ),
    (
        "connection dropped mid-mutation",
        lambda: transport_error("lab", httpx.ReadError("connection reset"), "purge cache"),
    ),
    (
        "500 after the request left",
        lambda: Transient("upstream 500", maybe_applied=True, status=500),
    ),
]

SAFE_TO_REPEAT: list[tuple[str, Callable[[], ConnectorError]]] = [
    (
        "rate limited before any work",
        lambda: RateLimited("provider is busy", retry_after=0.0, status=429),
    ),
    (
        "connection never established",
        lambda: transport_error("lab", httpx.ConnectTimeout("connect timed out"), "purge cache"),
    ),
]


def _chain_is_valid(case: dict[str, Any]) -> bool:
    return bool(case["chain"]["evidence_valid"]) and bool(case["chain"]["timeline_valid"])


@pytest.mark.parametrize(
    ("label", "failure"), MAYBE_APPLIED, ids=[item[0] for item in MAYBE_APPLIED]
)
def test_an_ambiguous_failure_is_never_retried_and_never_resolves(
    label: str,
    failure: Callable[[], ConnectorError],
    settings: Settings,
    lab: httpx.Client,
) -> None:
    app = create_app(settings.model_copy(update={"connector_retry_max_wait_seconds": 0.0}))
    service = app.state.service
    real_execute = service.registry.execute
    calls = {"count": 0}

    async def broken(action: ActionSpec) -> dict[str, Any]:
        calls["count"] += 1
        if calls["count"] == 1:
            raise failure()
        return await real_execute(action)

    service.registry.execute = broken  # type: ignore[method-assign]

    with TestClient(app) as client:
        incident = submit(client, scenario_payload(lab, "credential_leaked"))
        case = wait_for_status(client, incident["id"], {"awaiting_approval"})
        approval = case["approvals"][0]
        response = client.post(
            f"/v1/incidents/{incident['id']}/approvals/{approval['id']}",
            json={"approved": True, "comment": "go"},
        )
        assert response.status_code == 200, response.text

        failed = wait_for_status(client, incident["id"], {"failed"})
        assert failed["incident"]["status"] != "resolved"
        (action,) = failed["actions"]
        assert failed["action_states"][action["id"]] == "unknown"
        assert "may or may not have applied" in failed["incident"]["summary"]
        assert _chain_is_valid(failed)

        events = client.get(f"/v1/incidents/{incident['id']}/timeline").json()["events"]
        unknown = [event for event in events if event["event_type"] == "action.outcome_unknown"]
        assert unknown, f"{label}: the timeline did not record the ambiguous outcome"
        assert unknown[0]["payload"]["detail"]["maybe_applied"] is True

        # The provider was asked exactly once. A second attempt could create a
        # second deployment or revoke a second credential.
        assert calls["count"] == 1, f"{label}: the mutation was repeated"


@pytest.mark.parametrize(
    ("label", "failure"), SAFE_TO_REPEAT, ids=[item[0] for item in SAFE_TO_REPEAT]
)
def test_a_failure_that_cannot_have_applied_is_retried(
    label: str,
    failure: Callable[[], ConnectorError],
    settings: Settings,
    lab: httpx.Client,
) -> None:
    app = create_app(settings.model_copy(update={"connector_retry_max_wait_seconds": 0.0}))
    service = app.state.service
    real_execute = service.registry.execute
    calls = {"count": 0}

    async def flaky(action: ActionSpec) -> dict[str, Any]:
        calls["count"] += 1
        if calls["count"] <= 2:
            raise failure()
        return await real_execute(action)

    service.registry.execute = flaky  # type: ignore[method-assign]

    with TestClient(app) as client:
        incident = submit(client, scenario_payload(lab, "credential_leaked"))
        case = wait_for_status(client, incident["id"], {"awaiting_approval"})
        approval = case["approvals"][0]
        client.post(
            f"/v1/incidents/{incident['id']}/approvals/{approval['id']}",
            json={"approved": True, "comment": "go"},
        )

        resolved = wait_for_status(client, incident["id"], {"resolved"})
        assert resolved["incident"]["status"] == "resolved"
        assert _chain_is_valid(resolved)

        events = client.get(f"/v1/incidents/{incident['id']}/timeline").json()["events"]
        completed = [event for event in events if event["event_type"] == "action.completed"]
        assert completed
        attempts = completed[0]["payload"]["attempts"]
        assert len(attempts) == 2, f"{label}: expected two recorded retries, got {attempts}"
        assert all(attempt["waited_seconds"] == 0.0 for attempt in attempts)

    assert calls["count"] == 3, f"{label}: the operation did not run after the retries"
