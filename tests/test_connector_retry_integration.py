"""The executor's retry loop, end to end through the service.

The unit tests in ``test_retry.py`` prove the policy. This one proves the service
uses it: a connector that is rate limited twice still completes, and the timeline
records what happened instead of hiding the flakiness.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi.testclient import TestClient

from nightjar.api.main import create_app
from nightjar.api.settings import Settings
from nightjar.connectors.errors import RateLimited
from nightjar.core.models import ActionSpec
from tests.support import scenario_payload, submit, wait_for_status


def test_a_rate_limited_action_is_retried_and_recorded(
    settings: Settings, lab: httpx.Client
) -> None:
    settings = settings.model_copy(update={"connector_retry_max_wait_seconds": 0.0})
    app = create_app(settings)
    service = app.state.service
    real_execute = service.registry.execute
    calls = {"count": 0}

    async def flaky(action: ActionSpec) -> dict[str, Any]:
        calls["count"] += 1
        if calls["count"] <= 2:
            # The provider rejected the request before doing anything, so this is
            # the one failure that is always safe to repeat.
            raise RateLimited("provider is busy", retry_after=0.0, status=429)
        return await real_execute(action)

    service.registry.execute = flaky  # type: ignore[method-assign]

    with TestClient(app) as client:
        payload = scenario_payload(lab, "credential_leaked")
        incident = submit(client, payload)
        case = wait_for_status(client, incident["id"], {"awaiting_approval"})
        approval = case["approvals"][0]
        response = client.post(
            f"/v1/incidents/{incident['id']}/approvals/{approval['id']}",
            json={"approved": True, "comment": "go"},
        )
        assert response.status_code == 200, response.text
        resolved = wait_for_status(client, incident["id"], {"resolved"})
        assert resolved["incident"]["status"] == "resolved"

        events = client.get(f"/v1/incidents/{incident['id']}/timeline").json()["events"]
        completed = [event for event in events if event["event_type"] == "action.completed"]
        assert completed
        attempts = completed[0]["payload"]["attempts"]
        assert [attempt["kind"] for attempt in attempts] == ["rate_limited", "rate_limited"]
        assert all(attempt["waited_seconds"] == 0.0 for attempt in attempts)

    assert calls["count"] == 3


def test_a_failure_that_may_have_applied_is_not_retried(
    settings: Settings, lab: httpx.Client
) -> None:
    """A 500 on a mutation is retryable in principle and still must not repeat."""
    from nightjar.connectors.errors import Transient

    settings = settings.model_copy(update={"connector_retry_max_wait_seconds": 0.0})
    app = create_app(settings)
    service = app.state.service
    calls = {"count": 0}

    async def broken(action: ActionSpec) -> dict[str, Any]:
        calls["count"] += 1
        raise Transient("upstream 500", maybe_applied=True, status=500)

    service.registry.execute = broken  # type: ignore[method-assign]

    with TestClient(app) as client:
        payload = scenario_payload(lab, "credential_leaked")
        incident = submit(client, payload)
        case = wait_for_status(client, incident["id"], {"awaiting_approval"})
        approval = case["approvals"][0]
        client.post(
            f"/v1/incidents/{incident['id']}/approvals/{approval['id']}",
            json={"approved": True, "comment": "go"},
        )
        failed = wait_for_status(client, incident["id"], {"failed"})
        assert failed["incident"]["status"] == "failed"

        events = client.get(f"/v1/incidents/{incident['id']}/timeline").json()["events"]
        failure = [event for event in events if event["event_type"] == "action.failed"]
        assert failure
        assert failure[0]["payload"]["detail"]["maybe_applied"] is True

    assert calls["count"] == 1
