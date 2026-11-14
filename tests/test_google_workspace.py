from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import Response

from nightjar.connectors.base import ConnectorError
from nightjar.connectors.google_workspace import GoogleWorkspaceConnector
from nightjar.core.enums import ActionType, PredicateKind, RiskClass
from nightjar.core.models import ActionSpec, VerificationPredicate
from nightjar.core.verification import VerificationError

MAILBOX = "finance@cornershop.example"
ENCODED = "finance%40cornershop.example"
GMAIL = "https://gmail.googleapis.com"
OAUTH = "https://oauth2.googleapis.com/token"
LABEL_ID = "Label_42"


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def account_file(tmp_path: Path, private_key: rsa.RSAPrivateKey) -> str:
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    path = tmp_path / "service-account.json"
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "client_email": "nightjar@acme.iam.gserviceaccount.com",
                "private_key_id": "kid-1",
                "private_key": pem,
                "token_uri": OAUTH,
            }
        ),
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture
def connector(account_file: str) -> GoogleWorkspaceConnector:
    return GoogleWorkspaceConnector(account_file, mailboxes=(MAILBOX,))


def token_route() -> respx.Route:
    return respx.post(OAUTH).mock(
        return_value=Response(200, json={"access_token": "ya29.fake", "expires_in": 3600})
    )


def labels_route(labels: list[dict[str, str]] | None = None) -> respx.Route:
    return respx.get(f"{GMAIL}/gmail/v1/users/{ENCODED}/labels").mock(
        return_value=Response(200, json={"labels": labels or []})
    )


def quarantine_action(**params: object) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.QUARANTINE_MESSAGE,
        target="msg_1",
        params={"connector": "google", "message_id": "msg_1", **params},
        risk=RiskClass.R1,
    )


def block_action(**params: object) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.BLOCK_SENDER,
        target="bad@example.com",
        params={"connector": "google", "sender": "bad@example.com", **params},
        risk=RiskClass.R2,
    )


def predicate(check: str, **params: object) -> VerificationPredicate:
    return VerificationPredicate(
        kind=PredicateKind.CONNECTOR_CHECK,
        description="google check",
        params={"check": check, **params},
    )


# --------------------------------------------------------------------- auth
@respx.mock
async def test_the_assertion_is_a_delegated_rs256_jwt(
    connector: GoogleWorkspaceConnector, private_key: rsa.RSAPrivateKey
) -> None:
    route = token_route()
    labels_route([{"id": LABEL_ID, "name": "Nightjar-Quarantine"}])
    respx.post(f"{GMAIL}/gmail/v1/users/{ENCODED}/messages/msg_1/modify").mock(
        return_value=Response(200, json={"id": "msg_1"})
    )

    await connector.execute(quarantine_action())

    assertion = parse_qs(route.calls[0].request.content.decode())["assertion"][0]
    claims = jwt.decode(
        assertion,
        private_key.public_key(),
        algorithms=["RS256"],
        audience=OAUTH,
    )
    assert claims["iss"] == "nightjar@acme.iam.gserviceaccount.com"
    assert claims["sub"] == MAILBOX
    assert "gmail.modify" in claims["scope"]
    assert jwt.get_unverified_header(assertion)["kid"] == "kid-1"


@respx.mock
async def test_a_refused_delegation_names_the_setup_step(
    connector: GoogleWorkspaceConnector,
) -> None:
    respx.post(OAUTH).mock(
        return_value=Response(400, json={"error": "invalid_grant", "error_description": "no"})
    )
    with pytest.raises(ConnectorError, match="domain-wide delegation"):
        await connector.execute(quarantine_action())


# ---------------------------------------------------------------- quarantine
@respx.mock
async def test_quarantine_labels_the_message_and_removes_the_inbox(
    connector: GoogleWorkspaceConnector,
) -> None:
    token_route()
    labels_route([{"id": LABEL_ID, "name": "Nightjar-Quarantine"}])
    modify = respx.post(f"{GMAIL}/gmail/v1/users/{ENCODED}/messages/msg_1/modify").mock(
        return_value=Response(200, json={"id": "msg_1", "labelIds": [LABEL_ID]})
    )

    result = await connector.execute(quarantine_action())

    assert json.loads(modify.calls[0].request.content) == {
        "addLabelIds": [LABEL_ID],
        "removeLabelIds": ["INBOX", "UNREAD"],
    }
    assert result["mailbox"] == MAILBOX
    assert result["label_id"] == LABEL_ID


@respx.mock
async def test_a_missing_label_is_created_once(connector: GoogleWorkspaceConnector) -> None:
    token_route()
    labels_route([])
    create = respx.post(f"{GMAIL}/gmail/v1/users/{ENCODED}/labels").mock(
        return_value=Response(200, json={"id": LABEL_ID, "name": "Nightjar-Quarantine"})
    )
    respx.post(f"{GMAIL}/gmail/v1/users/{ENCODED}/messages/msg_1/modify").mock(
        return_value=Response(200, json={"id": "msg_1"})
    )

    await connector.execute(quarantine_action())

    body = json.loads(create.calls[0].request.content)
    assert body["name"] == "Nightjar-Quarantine"


# -------------------------------------------------------------- block sender
@respx.mock
async def test_block_sender_creates_a_filter(connector: GoogleWorkspaceConnector) -> None:
    token_route()
    labels_route([{"id": LABEL_ID, "name": "Nightjar-Quarantine"}])
    respx.get(f"{GMAIL}/gmail/v1/users/{ENCODED}/settings/filters").mock(
        return_value=Response(200, json={"filter": []})
    )
    create = respx.post(f"{GMAIL}/gmail/v1/users/{ENCODED}/settings/filters").mock(
        return_value=Response(200, json={"id": "filter_1"})
    )

    result = await connector.execute(block_action())

    body = json.loads(create.calls[0].request.content)
    assert body["criteria"] == {"from": "bad@example.com"}
    assert body["action"]["removeLabelIds"] == ["INBOX"]
    assert result["filter_id"] == "filter_1"
    assert result["replay"] is False


@respx.mock
async def test_block_sender_replays_an_existing_filter(
    connector: GoogleWorkspaceConnector,
) -> None:
    token_route()
    labels_route([{"id": LABEL_ID, "name": "Nightjar-Quarantine"}])
    respx.get(f"{GMAIL}/gmail/v1/users/{ENCODED}/settings/filters").mock(
        return_value=Response(
            200,
            json={
                "filter": [
                    {
                        "id": "filter_1",
                        "criteria": {"from": "BAD@example.com"},
                        "action": {"addLabelIds": [LABEL_ID]},
                    }
                ]
            },
        )
    )
    create = respx.post(f"{GMAIL}/gmail/v1/users/{ENCODED}/settings/filters").mock(
        return_value=Response(200, json={"id": "filter_2"})
    )

    result = await connector.execute(block_action())

    assert create.called is False
    assert result["replay"] is True
    assert result["filter_id"] == "filter_1"


# --------------------------------------------------------------- resolution
async def test_more_than_one_mailbox_requires_an_explicit_one(account_file: str) -> None:
    connector = GoogleWorkspaceConnector(account_file, mailboxes=("a@example.com", "b@example.com"))
    with pytest.raises(ConnectorError, match=r"params\.mailbox"):
        await connector.execute(quarantine_action())


async def test_no_configured_mailbox_is_a_clear_error(account_file: str) -> None:
    connector = GoogleWorkspaceConnector(account_file)
    with pytest.raises(ConnectorError, match="NIGHTJAR_MANAGED_TARGETS"):
        await connector.execute(quarantine_action())


# ------------------------------------------------------------- verification
@respx.mock
async def test_a_quarantined_message_passes_and_a_live_one_fails(
    connector: GoogleWorkspaceConnector,
) -> None:
    token_route()
    labels_route([{"id": LABEL_ID, "name": "Nightjar-Quarantine"}])
    route = respx.get(f"{GMAIL}/gmail/v1/users/{ENCODED}/messages/msg_1").mock(
        side_effect=[
            Response(200, json={"id": "msg_1", "labelIds": [LABEL_ID, "UNREAD"]}),
            Response(200, json={"id": "msg_1", "labelIds": [LABEL_ID, "INBOX"]}),
        ]
    )
    check = connector.verification_checks()["google.message_quarantined"]

    passed = await check(predicate("google.message_quarantined", message_id="msg_1"))
    failed = await check(predicate("google.message_quarantined", message_id="msg_1"))

    assert route.call_count == 2
    assert passed["passed"] is True
    assert failed["passed"] is False


@respx.mock
async def test_a_missing_filter_fails_verification(connector: GoogleWorkspaceConnector) -> None:
    token_route()
    labels_route([{"id": LABEL_ID, "name": "Nightjar-Quarantine"}])
    respx.get(f"{GMAIL}/gmail/v1/users/{ENCODED}/settings/filters").mock(
        return_value=Response(200, json={"filter": []})
    )

    observed = await connector.verification_checks()["google.filter_present"](
        predicate("google.filter_present", sender="bad@example.com")
    )
    assert observed["passed"] is False


async def test_a_check_without_its_identifier_is_a_verification_error(
    connector: GoogleWorkspaceConnector,
) -> None:
    with pytest.raises(VerificationError, match="message_id"):
        await connector.verification_checks()["google.message_quarantined"](
            predicate("google.message_quarantined")
        )


# ------------------------------------------------------------------ preflight
@respx.mock
async def test_preflight_proves_the_delegated_mailbox(
    connector: GoogleWorkspaceConnector,
) -> None:
    token_route()
    respx.get(f"{GMAIL}/gmail/v1/users/{ENCODED}/profile").mock(
        return_value=Response(200, json={"emailAddress": MAILBOX, "messagesTotal": 42})
    )
    report = await connector.preflight()
    assert report.verdict == "ok"
    assert report.identity == f"delegated:{MAILBOX}"


async def test_preflight_without_a_mailbox_is_degraded(account_file: str) -> None:
    report = await GoogleWorkspaceConnector(account_file).preflight()
    assert report.verdict == "degraded"
    assert "no mailboxes" in report.detail


@respx.mock
async def test_preflight_reports_a_rejected_token(connector: GoogleWorkspaceConnector) -> None:
    respx.post(OAUTH).mock(return_value=Response(401, json={"error": "invalid_client"}))
    report = await connector.preflight()
    assert report.verdict == "error"
    assert "delegation" in report.detail


# ---------------------------------------------------------------- read tool
@respx.mock
async def test_reading_a_message_returns_headers_as_evidence(
    connector: GoogleWorkspaceConnector,
) -> None:
    token_route()
    respx.get(f"{GMAIL}/gmail/v1/users/{ENCODED}/messages/msg_1").mock(
        return_value=Response(
            200,
            json={
                "id": "msg_1",
                "threadId": "thread_1",
                "labelIds": ["INBOX"],
                "snippet": "confirm your payout",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "bad@example.com"},
                        {"name": "Subject", "value": "Payout failed"},
                    ]
                },
            },
        )
    )
    evidence = await connector.collect("google.message", {"message_id": "msg_1"})
    assert evidence.source == f"google:{MAILBOX}/msg_1"
    assert evidence.content["from"] == "bad@example.com"
    assert evidence.content["subject"] == "Payout failed"


def test_capabilities_cover_both_mail_actions(connector: GoogleWorkspaceConnector) -> None:
    capabilities = connector.capabilities()
    assert {item.action_type for item in capabilities} == {
        ActionType.QUARANTINE_MESSAGE,
        ActionType.BLOCK_SENDER,
    }
    assert all(item.automated for item in capabilities)


def test_an_unreadable_account_file_is_reported_without_a_traceback(tmp_path: Path) -> None:
    connector = GoogleWorkspaceConnector(str(tmp_path / "missing.json"))
    with pytest.raises(ConnectorError, match="cannot read"):
        connector._load_account()


def test_a_malformed_account_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "sa.json"
    path.write_text("{}", encoding="utf-8")
    connector = GoogleWorkspaceConnector(str(path))
    with pytest.raises(ConnectorError, match="missing"):
        connector._load_account()


def test_the_connector_never_exposes_the_private_key(connector: GoogleWorkspaceConnector) -> None:
    assert "PRIVATE KEY" not in repr(connector)
