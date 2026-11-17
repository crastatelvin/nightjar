"""Separation of duties: the person who filed an incident cannot approve its fix.

This is the control that makes approvals meaningful once more than one person has
access. It is opt-in, because a one-person operations team is the product's target
and a two-person rule would lock the only operator out; the test that documents
that trade-off is as important as the one that enforces the rule.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from nightjar.api.main import create_app
from nightjar.api.settings import Settings
from nightjar.core.signatures import sign
from tests.support import get_case, scenario_payload, wait_for_status

TOKEN = "shared-token-for-tests"
INTAKE_SECRET = "intake-secret-" + "0" * 32


def settings_for(settings: Settings, *, separation: bool) -> Settings:
    return settings.model_copy(
        update={
            "api_token": TOKEN,
            "require_separation_of_duties": separation,
        }
    )


def submit_as(client: TestClient, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
    response = client.post(
        "/v1/incidents",
        params={"wait": "true"},
        json=payload,
        headers={"authorization": f"Bearer {TOKEN}", "x-nightjar-actor": actor},
    )
    assert response.status_code == 201, response.text
    return response.json()


def approve(
    client: TestClient, incident_id: str, approval_id: str, *, actor: str
) -> httpx.Response:
    return client.post(
        f"/v1/incidents/{incident_id}/approvals/{approval_id}",
        json={"approved": True, "comment": "looks right"},
        headers={"authorization": f"Bearer {TOKEN}", "x-nightjar-actor": actor},
    )


def authenticated(app: Any) -> TestClient:
    """A client that carries the shared token, so helpers need no headers."""
    return TestClient(app, headers={"authorization": f"Bearer {TOKEN}"})


@pytest.fixture
def enforcing(settings: Settings, lab: httpx.Client) -> Any:
    with authenticated(create_app(settings_for(settings, separation=True))) as client:
        yield client


@pytest.fixture
def permissive(settings: Settings, lab: httpx.Client) -> Any:
    with authenticated(create_app(settings_for(settings, separation=False))) as client:
        yield client


def test_the_reporter_cannot_approve_its_own_remediation(
    enforcing: TestClient, lab: httpx.Client
) -> None:
    payload = scenario_payload(lab, "credential_leaked")
    incident = submit_as(enforcing, payload, actor="alice")
    case = wait_for_status(enforcing, incident["id"], {"awaiting_approval"})
    approval = case["approvals"][0]

    response = approve(enforcing, incident["id"], approval["id"], actor="alice")

    assert response.status_code == 403
    assert "cannot approve its remediation" in response.json()["detail"]
    # Nothing was executed on the refusal.
    credential = lab.get(f"/lab/credentials/{payload['metadata']['credential_id']}").json()
    assert credential["status"] == "active"


def test_a_second_operator_can_approve(enforcing: TestClient, lab: httpx.Client) -> None:
    payload = scenario_payload(lab, "credential_leaked")
    incident = submit_as(enforcing, payload, actor="alice")
    case = wait_for_status(enforcing, incident["id"], {"awaiting_approval"})
    approval = case["approvals"][0]

    response = approve(enforcing, incident["id"], approval["id"], actor="bob")

    assert response.status_code == 200, response.text
    resolved = wait_for_status(enforcing, incident["id"], {"resolved"})
    assert resolved["incident"]["status"] == "resolved"
    assert lab.get("/lab/audit").json()["healthy"] is True


def test_the_approval_records_who_asked_and_who_decided(
    enforcing: TestClient, lab: httpx.Client
) -> None:
    payload = scenario_payload(lab, "credential_leaked")
    incident = submit_as(enforcing, payload, actor="alice")
    case = wait_for_status(enforcing, incident["id"], {"awaiting_approval"})
    approval = case["approvals"][0]
    assert approval["requested_by"] == "alice"

    approve(enforcing, incident["id"], approval["id"], actor="bob")

    decided = get_case(enforcing, incident["id"])["approvals"][0]
    assert decided["approved_by"] == "bob"
    assert decided["requested_by"] == "alice"


def test_the_decision_is_auditable(enforcing: TestClient, lab: httpx.Client) -> None:
    payload = scenario_payload(lab, "credential_leaked")
    incident = submit_as(enforcing, payload, actor="alice")
    case = wait_for_status(enforcing, incident["id"], {"awaiting_approval"})
    approve(enforcing, incident["id"], case["approvals"][0]["id"], actor="bob")

    events = enforcing.get(f"/v1/incidents/{incident['id']}/timeline").json()["events"]
    decided = [event for event in events if event["event_type"] == "approval.decided"]

    assert decided
    assert decided[0]["payload"]["requested_by"] == "alice"
    assert decided[0]["payload"]["separation_of_duties"] is True
    assert decided[0]["actor"] == "bob"


def test_a_single_operator_can_still_approve_when_the_rule_is_off(
    permissive: TestClient, lab: httpx.Client
) -> None:
    """The SMB default: one person is the whole operations team."""
    payload = scenario_payload(lab, "credential_leaked")
    incident = submit_as(permissive, payload, actor="alice")
    case = wait_for_status(permissive, incident["id"], {"awaiting_approval"})

    response = approve(permissive, incident["id"], case["approvals"][0]["id"], actor="alice")

    assert response.status_code == 200, response.text
    assert wait_for_status(permissive, incident["id"], {"resolved"})["incident"]["status"] == (
        "resolved"
    )


def test_an_alert_filed_by_the_webhook_can_be_approved_by_a_human(
    enforcing: TestClient, lab: httpx.Client
) -> None:
    """A webhook reporter is not a person, so it can never be the approver."""
    settings = enforcing.app.state.settings
    original = settings.intake_webhook_secret
    settings.intake_webhook_secret = INTAKE_SECRET
    seed = scenario_payload(lab, "credential_leaked")
    body = json.dumps(
        {
            "title": seed["title"],
            "description": seed["description"],
            "incident_class": seed["incident_class"],
            "severity": seed["severity"],
            "metadata": seed["metadata"],
        }
    ).encode()
    moment = int(time.time())
    try:
        accepted = enforcing.post(
            "/v1/intake/webhook",
            content=body,
            headers={
                "content-type": "application/json",
                "x-nightjar-signature": f"t={moment},v1={sign(INTAKE_SECRET, moment, body)}",
                "x-nightjar-delivery": "dlv-sod",
            },
        )
    finally:
        settings.intake_webhook_secret = original
    assert accepted.status_code == 202, accepted.text
    incident_id = accepted.json()["incident_id"]

    case = wait_for_status(enforcing, incident_id, {"awaiting_approval"})
    approval = case["approvals"][0]
    assert case["incident"]["reported_by"] == "alert:webhook"
    assert approval["requested_by"] == "alert:webhook"

    response = approve(enforcing, incident_id, approval["id"], actor="alice")

    assert response.status_code == 200, response.text
