from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from nightjar.api.main import create_app
from nightjar.api.settings import Settings
from tests.support import (
    all_required_passed,
    approve_pending,
    scenario_payload,
    submit,
    wait_for_status,
)

SCENARIOS = [
    ("site_cache_poisoned", 0),
    ("site_bad_deploy", 1),
    ("site_db_down", 2),
    ("credential_leaked", 3),
    ("phishing_report", 4),
]


@pytest.mark.integration
@pytest.mark.parametrize(("scenario", "variant"), SCENARIOS)
def test_every_failure_mode_reaches_a_verified_resolution(
    client: TestClient, lab, scenario: str, variant: int
) -> None:
    payload = scenario_payload(lab, scenario, variant=variant)
    incident = submit(client, payload)
    wait_for_status(client, incident["id"], {"awaiting_approval", "resolved"})
    approve_pending(client, incident["id"])
    case = wait_for_status(client, incident["id"], {"resolved"})

    assert case["incident"]["status"] == "resolved"
    assert case["verifications"], "a resolution without verification is not a resolution"
    assert all_required_passed(case)
    assert case["chain"]["evidence_valid"] is True
    assert case["chain"]["timeline_valid"] is True
    assert lab.get("/lab/audit").json()["healthy"] is True


@pytest.mark.integration
def test_read_only_cache_fix_does_not_require_approval(client: TestClient, lab) -> None:
    payload = scenario_payload(lab, "site_cache_poisoned")
    incident = submit(client, payload)
    case = wait_for_status(client, incident["id"], {"resolved"})
    assert case["approvals"] == []
    assert all(state == "executed" for state in case["action_states"].values())


@pytest.mark.integration
def test_customer_impacting_fix_waits_for_an_operator(client: TestClient, lab) -> None:
    payload = scenario_payload(lab, "site_db_down")
    incident = submit(client, payload)
    case = wait_for_status(client, incident["id"], {"awaiting_approval"})

    assert len(case["approvals"]) == 1
    assert case["approvals"][0]["status"] == "pending"
    assert list(case["action_states"].values()) == ["awaiting_approval"]
    # The environment is untouched while the approval is pending.
    audit = lab.get("/lab/audit").json()
    assert audit["checks"]["checkout"] is False

    approve_pending(client, incident["id"])
    resolved = wait_for_status(client, incident["id"], {"resolved"})
    assert all_required_passed(resolved)


@pytest.mark.integration
def test_rejection_leaves_the_environment_broken(client: TestClient, lab) -> None:
    payload = scenario_payload(lab, "credential_leaked")
    incident = submit(client, payload)
    case = wait_for_status(client, incident["id"], {"awaiting_approval"})
    approval = case["approvals"][0]
    response = client.post(
        f"/v1/incidents/{incident['id']}/approvals/{approval['id']}",
        json={"approved": False, "comment": "wrong key"},
    )
    assert response.status_code == 200
    rejected = response.json()

    assert rejected["incident"]["status"] == "failed"
    assert rejected["action_states"][case["actions"][0]["id"]] == "denied"
    credential_id = payload["metadata"]["credential_id"]
    assert lab.get(f"/lab/credentials/{credential_id}").json()["status"] == "active"
    assert lab.get("/lab/audit").json()["healthy"] is False


@pytest.mark.integration
def test_relapse_reopens_the_incident(settings: Settings, lab) -> None:
    unstable = settings.model_copy(update={"stability_seconds": 1})
    app = create_app(unstable)
    with TestClient(app) as client:
        payload = scenario_payload(lab, "site_cache_poisoned", relapse=True)
        incident = submit(client, payload)
        wait_for_status(client, incident["id"], {"verifying"})

        case = wait_for_status(client, incident["id"], {"resolved", "reopened"}, timeout=30)
        assert case["incident"]["status"] == "reopened"

        phases = {(item["phase"], item["passed"]) for item in case["verifications"]}
        assert ("t0", True) in phases
        assert ("stability", False) in phases
        assert lab.get("/lab/audit").json()["healthy"] is False


@pytest.mark.integration
def test_relapse_is_not_reported_as_resolved_even_briefly(settings: Settings, lab) -> None:
    """The T+0 pass must never be exposed as a resolution before the stability window."""
    unstable = settings.model_copy(update={"stability_seconds": 1})
    app = create_app(unstable)
    with TestClient(app) as client:
        payload = scenario_payload(lab, "site_cache_poisoned", relapse=True)
        incident = submit(client, payload, wait=False)
        deadline = time.monotonic() + 20
        seen: set[str] = set()
        while time.monotonic() < deadline:
            status = client.get(f"/v1/incidents/{incident['id']}").json()["incident"]["status"]
            seen.add(status)
            if status in {"reopened", "failed"}:
                break
            time.sleep(0.1)
        assert "resolved" not in seen
        assert "reopened" in seen
