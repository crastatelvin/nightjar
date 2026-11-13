from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from nightjar.api.main import create_app
from nightjar.api.settings import Settings
from tests.support import approve_pending, get_case, scenario_payload, submit, wait_for_status


def test_health_and_readiness(client: TestClient) -> None:
    assert client.get("/healthz").json()["status"] == "ok"
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["checks"]["database"] is True


def test_meta_lists_the_typed_catalog(client: TestClient) -> None:
    meta = client.get("/v1/meta").json()
    assert meta["provider"] == "mock"
    assert "lab" in meta["connectors"]
    assert "lab.site_state" in meta["tools"]
    assert meta["action_catalog"]["purge_cache"] == "r1"
    assert "transfer_money" not in meta["action_catalog"]


def test_bearer_auth_is_enforced(settings: Settings) -> None:
    secured = settings.model_copy(update={"api_token": "correct-horse"})
    app = create_app(secured)
    with TestClient(app) as client:
        assert client.get("/v1/meta").status_code == 401
        assert client.get("/v1/meta", headers={"authorization": "Bearer wrong"}).status_code == 403
        assert (
            client.get("/v1/meta", headers={"authorization": "Bearer correct-horse"}).status_code
            == 200
        )
        # Health checks stay unauthenticated so orchestrators can probe them.
        assert client.get("/healthz").status_code == 200


def test_incident_validation_is_strict(client: TestClient) -> None:
    response = client.post("/v1/incidents", json={"title": "x"})
    assert response.status_code == 422


def test_unknown_incident_returns_404(client: TestClient) -> None:
    assert client.get("/v1/incidents/does-not-exist").status_code == 404
    assert client.get("/v1/incidents/does-not-exist/chain").status_code == 404


def test_cache_incident_closes_the_loop(client: TestClient, lab) -> None:
    payload = scenario_payload(lab, "site_cache_poisoned")
    incident = submit(client, payload)
    case = wait_for_status(client, incident["id"], {"resolved"})

    assert case["plan"] is not None
    assert case["findings"], "the investigator must record at least one finding"
    assert all(finding["claims"] for finding in case["findings"])
    assert case["evidence"], "verification must produce evidence"
    assert case["timeline"][0]["event_type"] == "incident.created"
    event_types = {event["event_type"] for event in case["timeline"]}
    assert {
        "triage.completed",
        "finding.recorded",
        "plan.proposed",
        "policy.decided",
        "action.started",
        "action.completed",
        "verification.completed",
        "incident.resolved",
    } <= event_types
    assert case["chain"]["evidence_valid"] is True
    assert case["chain"]["timeline_valid"] is True
    assert case["chain"]["evidence_checked"] > 0
    assert case["chain"]["timeline_checked"] > 0
    assert lab.get("/lab/audit").json()["healthy"] is True


def test_dedupe_key_returns_the_same_incident(client: TestClient, lab) -> None:
    payload = scenario_payload(lab, "credential_leaked")
    first = submit(client, payload, wait=False, dedupe_key="stripe-key-rotation")
    second = submit(client, payload, wait=False, dedupe_key="stripe-key-rotation")
    assert first["id"] == second["id"]

    listing = client.get("/v1/incidents").json()
    matching = [item for item in listing["items"] if item["id"] == first["id"]]
    assert len(matching) == 1


def test_operator_notes_are_kept_on_the_case_file(client: TestClient, lab) -> None:
    payload = scenario_payload(lab, "site_cache_poisoned")
    incident = submit(client, payload)
    response = client.post(
        f"/v1/incidents/{incident['id']}/notes",
        json={"body": "Talked to the on-call owner."},
        headers={"x-nightjar-actor": "alice"},
    )
    assert response.status_code == 200
    notes = response.json()["notes"]
    assert notes[-1]["author"] == "alice"
    assert notes[-1]["body"] == "Talked to the on-call owner."

    # The note is part of the ledger, not a side table: the live stream pushes
    # timeline entries, and an unaudited note would be a claim without a hash.
    timeline = client.get(f"/v1/incidents/{incident['id']}/timeline").json()["events"]
    noted = [event for event in timeline if event["event_type"] == "note"]
    assert len(noted) == 1
    assert noted[0]["actor"] == "alice"
    assert noted[0]["payload"]["body"] == "Talked to the on-call owner."
    assert noted[0]["payload"]["note_id"] == notes[-1]["id"]
    assert noted[0]["prev_hash"] == timeline[-2]["hash"]
    assert client.get(f"/v1/incidents/{incident['id']}/chain").json()["timeline_valid"] is True


def test_timeline_can_be_read_incrementally(client: TestClient, lab) -> None:
    payload = scenario_payload(lab, "site_cache_poisoned")
    incident = submit(client, payload)
    full = client.get(f"/v1/incidents/{incident['id']}/timeline").json()["events"]
    assert full
    tail = client.get(
        f"/v1/incidents/{incident['id']}/timeline",
        params={"after_seq": full[0]["seq"]},
    ).json()["events"]
    assert len(tail) == len(full) - 1


def test_chain_detects_tampering(client: TestClient, lab, database_url: str) -> None:
    payload = scenario_payload(lab, "site_cache_poisoned")
    incident = submit(client, payload)
    case = get_case(client, incident["id"])
    assert case["chain"]["evidence_valid"] is True

    async def tamper() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE evidence SET summary = summary || ' tampered' "
                        "WHERE incident_id = :incident_id"
                    ),
                    {"incident_id": incident["id"]},
                )
        finally:
            await engine.dispose()

    asyncio.run(tamper())
    chain = client.get(f"/v1/incidents/{incident['id']}/chain").json()
    assert chain["evidence_valid"] is False
    assert chain["broken_at"] is not None


def test_approval_expiry_fails_closed(settings: Settings, lab) -> None:
    expired = settings.model_copy(update={"approval_ttl_seconds": 0})
    app = create_app(expired)
    with TestClient(app) as client:
        payload = scenario_payload(lab, "credential_leaked")
        incident = submit(client, payload)
        case = wait_for_status(client, incident["id"], {"awaiting_approval"})
        approval = case["approvals"][0]
        response = client.post(
            f"/v1/incidents/{incident['id']}/approvals/{approval['id']}",
            json={"approved": True, "comment": "too late"},
        )
        assert response.status_code == 409
        # Nothing was executed while the approval was pending.
        credential_id = payload["metadata"]["credential_id"]
        credential = lab.get(f"/lab/credentials/{credential_id}").json()
        assert credential["status"] == "active"


def test_rejected_incident_can_be_retried(settings: Settings, lab) -> None:
    payload = scenario_payload(lab, "credential_leaked")
    app = create_app(settings)
    with TestClient(app) as client:
        incident = submit(client, payload)
        case = wait_for_status(client, incident["id"], {"awaiting_approval"})
        approval = case["approvals"][0]
        rejected = client.post(
            f"/v1/incidents/{incident['id']}/approvals/{approval['id']}",
            json={"approved": False, "comment": "not this key"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["incident"]["status"] == "failed"

        # The lab is still broken: rejection must not execute anything.
        assert lab.get("/lab/audit").json()["checks"]["no_unrevoked_exposed_credentials"] is False

        retry = client.post(f"/v1/incidents/{incident['id']}/retry")
        assert retry.status_code == 200
        wait_for_status(client, incident["id"], {"awaiting_approval"})
        approve_pending(client, incident["id"])
        resolved = wait_for_status(client, incident["id"], {"resolved"})
        assert resolved["incident"]["status"] == "resolved"
        assert lab.get("/lab/audit").json()["healthy"] is True


def test_retry_rejects_a_resolved_incident(client: TestClient, lab) -> None:
    payload = scenario_payload(lab, "site_cache_poisoned")
    incident = submit(client, payload)
    response = client.post(f"/v1/incidents/{incident['id']}/retry")
    assert response.status_code == 409


@pytest.mark.integration
def test_list_filters_by_status(client: TestClient, lab) -> None:
    payload = scenario_payload(lab, "site_cache_poisoned")
    submit(client, payload)
    resolved = client.get("/v1/incidents", params={"status": "resolved"}).json()
    assert resolved["items"]
    failed = client.get("/v1/incidents", params={"status": "failed"}).json()
    assert failed["total"] == 0
