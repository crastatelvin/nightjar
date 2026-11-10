"""Helpers shared by integration tests."""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi.testclient import TestClient

SEED_FIELDS = ("title", "description", "incident_class", "severity", "source", "metadata")
TERMINAL_STATUSES = {"resolved", "failed", "reopened", "cancelled"}


def scenario_payload(
    lab: httpx.Client, scenario: str, *, variant: int = 0, relapse: bool = False
) -> dict[str, Any]:
    lab.post("/lab/reset")
    response = lab.post(
        f"/lab/scenario/{scenario}",
        params={"variant": variant, "relapse": str(relapse).lower()},
    )
    response.raise_for_status()
    seed = response.json()
    return {field: seed[field] for field in SEED_FIELDS}


def submit(
    client: TestClient,
    payload: dict[str, Any],
    *,
    wait: bool = True,
    dedupe_key: str | None = None,
) -> dict[str, Any]:
    body = dict(payload)
    if dedupe_key is not None:
        body["dedupe_key"] = dedupe_key
    response = client.post("/v1/incidents", params={"wait": str(wait).lower()}, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def get_case(client: TestClient, incident_id: str) -> dict[str, Any]:
    response = client.get(f"/v1/incidents/{incident_id}")
    assert response.status_code == 200, response.text
    return response.json()


def wait_for_status(
    client: TestClient,
    incident_id: str,
    statuses: set[str],
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    case = get_case(client, incident_id)
    while time.monotonic() < deadline:
        if case["incident"]["status"] in statuses:
            return case
        time.sleep(0.2)
        case = get_case(client, incident_id)
    raise AssertionError(
        f"incident {incident_id} stayed {case['incident']['status']}, expected one of {statuses}"
    )


def approve_pending(client: TestClient, incident_id: str) -> dict[str, Any]:
    """Approve every pending request until none remain. Returns the final case."""
    seen: set[str] = set()
    for _ in range(10):
        case = get_case(client, incident_id)
        pending = [
            approval
            for approval in case["approvals"]
            if approval["status"] == "pending" and approval["id"] not in seen
        ]
        if not pending:
            return case
        for approval in pending:
            seen.add(approval["id"])
            response = client.post(
                f"/v1/incidents/{incident_id}/approvals/{approval['id']}",
                json={"approved": True, "comment": "integration test approval"},
            )
            assert response.status_code == 200, response.text
    raise AssertionError("approvals kept appearing; refusing to loop forever")


def pending_approvals(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in case["approvals"] if item["status"] == "pending"]


def required_verifications(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in case["verifications"] if item["required"]]


def all_required_passed(case: dict[str, Any]) -> bool:
    current: dict[str, bool] = {}
    for verification in case["verifications"]:
        if verification["required"]:
            current[verification["predicate_id"]] = verification["passed"]
    return bool(current) and all(current.values())
