from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nightjar.core.signatures import sign

SECRET = "intake-secret-" + "0" * 32
PATH = "/v1/intake/webhook"


@pytest.fixture
def webhook_secret(client: TestClient) -> Iterator[str]:
    settings = client.app.state.settings
    original = settings.intake_webhook_secret
    settings.intake_webhook_secret = SECRET
    try:
        yield SECRET
    finally:
        settings.intake_webhook_secret = original


def alert(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Checkout is returning 502",
        "description": "The status page opened at 09:14.",
        "incident_class": "site_outage",
        "severity": "high",
        "metadata": {"source": "uptime-monitor"},
    }
    payload.update(overrides)
    return payload


def signed_headers(
    secret: str,
    body: bytes,
    *,
    timestamp: int | None = None,
    delivery: str | None = "dlv-1",
    scheme: str = "combined",
) -> dict[str, str]:
    moment = int(time.time()) if timestamp is None else timestamp
    digest = sign(secret, moment, body)
    headers = {"content-type": "application/json"}
    if scheme == "combined":
        headers["x-nightjar-signature"] = f"t={moment},v1={digest}"
    elif scheme == "separate":
        headers["x-nightjar-signature"] = f"v1={digest}"
        headers["x-nightjar-timestamp"] = str(moment)
    else:
        headers["x-nightjar-signature"] = digest
        headers["x-nightjar-timestamp"] = str(moment)
    if delivery is not None:
        headers["x-nightjar-delivery"] = delivery
    return headers


def post(client: TestClient, secret: str, payload: dict[str, Any], **kwargs: Any) -> Any:
    body = json.dumps(payload).encode()
    return client.post(PATH, content=body, headers=signed_headers(secret, body, **kwargs))


def incident_count(client: TestClient) -> int:
    return int(client.get("/v1/incidents").json()["total"])


def test_a_signed_alert_files_an_incident(client: TestClient, webhook_secret: str) -> None:
    response = post(client, webhook_secret, alert())

    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["delivery_id"] == "dlv-1"
    assert accepted["deduplicated"] is False

    case = client.get(f"/v1/incidents/{accepted['incident_id']}")
    assert case.status_code == 200
    incident = case.json()["incident"]
    assert incident["source"] == "webhook"
    assert incident["title"] == "Checkout is returning 502"
    assert incident["severity"] == "high"
    assert incident["dedupe_key"] == "webhook:dlv-1"


def test_the_timestamp_may_be_sent_separately(client: TestClient, webhook_secret: str) -> None:
    response = post(client, webhook_secret, alert(), scheme="separate")
    assert response.status_code == 202, response.text


def test_a_bare_digest_with_a_timestamp_header_is_accepted(
    client: TestClient, webhook_secret: str
) -> None:
    response = post(client, webhook_secret, alert(), scheme="bare")
    assert response.status_code == 202, response.text


def test_a_bad_signature_is_rejected_and_files_nothing(
    client: TestClient, webhook_secret: str
) -> None:
    body = json.dumps(alert()).encode()
    headers = signed_headers(webhook_secret, body)
    headers["x-nightjar-signature"] = f"t={int(time.time())},v1={'0' * 64}"

    response = client.post(PATH, content=body, headers=headers)

    assert response.status_code == 401
    assert "does not match" in response.json()["detail"]
    assert incident_count(client) == 0


def test_a_missing_signature_is_rejected(client: TestClient, webhook_secret: str) -> None:
    response = client.post(
        PATH,
        content=json.dumps(alert()).encode(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 401
    assert incident_count(client) == 0


def test_a_body_modified_after_signing_is_rejected(client: TestClient, webhook_secret: str) -> None:
    signed_body = json.dumps(alert()).encode()
    headers = signed_headers(webhook_secret, signed_body)
    tampered = json.dumps(alert(severity="low")).encode()

    response = client.post(PATH, content=tampered, headers=headers)

    assert response.status_code == 401
    assert incident_count(client) == 0


def test_a_stale_timestamp_is_rejected(client: TestClient, webhook_secret: str) -> None:
    response = post(client, webhook_secret, alert(), timestamp=int(time.time()) - 301)
    assert response.status_code == 401
    assert "tolerance" in response.json()["detail"]
    assert incident_count(client) == 0


def test_a_future_timestamp_is_rejected(client: TestClient, webhook_secret: str) -> None:
    response = post(client, webhook_secret, alert(), timestamp=int(time.time()) + 301)
    assert response.status_code == 401


def test_a_replayed_delivery_is_rejected(client: TestClient, webhook_secret: str) -> None:
    first = post(client, webhook_secret, alert(), delivery="dlv-replay")
    assert first.status_code == 202

    second = post(client, webhook_secret, alert(), delivery="dlv-replay")

    assert second.status_code == 409
    assert "already accepted" in second.json()["detail"]
    assert incident_count(client) == 1


def test_a_replay_without_a_delivery_header_is_rejected(
    client: TestClient, webhook_secret: str
) -> None:
    first = post(client, webhook_secret, alert(), delivery=None)
    assert first.status_code == 202

    second = post(client, webhook_secret, alert(), delivery=None)

    assert second.status_code == 409
    assert incident_count(client) == 1


def test_an_explicit_dedupe_key_collapses_two_deliveries(
    client: TestClient, webhook_secret: str
) -> None:
    first = post(client, webhook_secret, alert(dedupe_key="checkout-502"), delivery="dlv-a")
    second = post(client, webhook_secret, alert(dedupe_key="checkout-502"), delivery="dlv-b")

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["deduplicated"] is True
    assert second.json()["incident_id"] == first.json()["incident_id"]
    assert incident_count(client) == 1


def test_the_endpoint_is_disabled_without_a_secret(client: TestClient) -> None:
    assert client.app.state.settings.intake_webhook_secret is None

    response = client.post(
        PATH,
        content=json.dumps(alert()).encode(),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 503
    assert "NIGHTJAR_INTAKE_WEBHOOK_SECRET" in response.json()["detail"]
    assert incident_count(client) == 0


def test_an_oversized_body_is_rejected_before_verification(
    client: TestClient, webhook_secret: str
) -> None:
    body = json.dumps(alert(description="x" * 70_000)).encode()
    headers = signed_headers(webhook_secret, body)

    response = client.post(PATH, content=body, headers=headers)

    assert response.status_code == 413
    assert "65536" in response.json()["detail"]
    assert incident_count(client) == 0


def test_malformed_json_is_rejected_after_the_signature_passes(
    client: TestClient, webhook_secret: str
) -> None:
    body = b"{not json"
    response = client.post(PATH, content=body, headers=signed_headers(webhook_secret, body))
    assert response.status_code == 400
    assert "not valid JSON" in response.json()["detail"]


def test_a_non_object_body_is_rejected(client: TestClient, webhook_secret: str) -> None:
    body = b'["not", "an", "object"]'
    response = client.post(PATH, content=body, headers=signed_headers(webhook_secret, body))
    assert response.status_code == 400
    assert "JSON object" in response.json()["detail"]


def test_an_invalid_payload_is_rejected_with_422(client: TestClient, webhook_secret: str) -> None:
    response = post(client, webhook_secret, alert(title="no"))
    assert response.status_code == 422
    assert incident_count(client) == 0


def test_a_secret_is_never_echoed_in_an_error(client: TestClient, webhook_secret: str) -> None:
    body = json.dumps(alert()).encode()
    headers = signed_headers(webhook_secret, body)
    headers["x-nightjar-signature"] = f"t={int(time.time())},v1={'1' * 64}"

    response = client.post(PATH, content=body, headers=headers)

    assert SECRET not in response.text


def test_a_chunked_body_over_the_cap_is_rejected(client: TestClient, webhook_secret: str) -> None:
    body = json.dumps(alert(description="x" * 70_000)).encode()
    headers = signed_headers(webhook_secret, body)

    def chunks() -> Iterator[bytes]:
        for offset in range(0, len(body), 8192):
            yield body[offset : offset + 8192]

    response = client.post(PATH, content=chunks(), headers=headers)

    assert response.status_code == 413
    assert incident_count(client) == 0


def test_the_replay_store_is_shared_between_replicas(
    client: TestClient, webhook_secret: str
) -> None:
    """Two API processes, one database: a capture replayed at the other replica dies."""
    from nightjar.api.main import create_app

    settings = client.app.state.settings
    first = post(client, webhook_secret, alert(), delivery="dlv-shared")
    assert first.status_code == 202

    with TestClient(create_app(settings)) as other:
        second = post(other, webhook_secret, alert(), delivery="dlv-shared")

    assert second.status_code == 409
    assert "already accepted" in second.json()["detail"]


def test_a_failed_filing_releases_the_delivery(client: TestClient, webhook_secret: str) -> None:
    """A server error must not burn the sender's delivery id and lose the alert."""
    service = client.app.state.service
    original = service.intake_event

    async def boom(**kwargs: Any) -> Any:
        raise RuntimeError("database is down")

    service.intake_event = boom
    try:
        with pytest.raises(RuntimeError, match="database is down"):
            post(client, webhook_secret, alert(), delivery="dlv-retry")
    finally:
        service.intake_event = original

    retry = post(client, webhook_secret, alert(), delivery="dlv-retry")

    assert retry.status_code == 202
    assert incident_count(client) == 1
