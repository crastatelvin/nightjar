from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from httpx import Response

from nightjar.connectors.base import ConnectorError
from nightjar.connectors.microsoft365 import Microsoft365Connector
from nightjar.core.enums import ActionType, PredicateKind, RiskClass
from nightjar.core.models import ActionSpec, VerificationPredicate
from nightjar.core.verification import VerificationError

TENANT = "contoso.onmicrosoft.com"
MAILBOX = "owner@cornershop.example"
ENCODED = "owner%40cornershop.example"
LOGIN = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
GRAPH = "https://graph.microsoft.com/v1.0"
USER = f"{GRAPH}/users/{ENCODED}"
FOLDERS = f"{USER}/mailFolders"
RULES = f"{USER}/mailFolders/inbox/messageRules"
FOLDER_ID = "AAMkAD-quarantine"
INBOX_ID = "AAMkAD-inbox"


@pytest.fixture
def connector() -> Microsoft365Connector:
    return Microsoft365Connector(
        TENANT, "client-id", "client-secret", mailboxes=(MAILBOX,), propagation_seconds=60
    )


def token_route() -> respx.Route:
    return respx.post(LOGIN).mock(
        return_value=Response(200, json={"access_token": "graph.token", "expires_in": 3600})
    )


def folders_route(folders: list[dict[str, str]] | None = None) -> respx.Route:
    return respx.get(FOLDERS).mock(return_value=Response(200, json={"value": folders or []}))


def quarantine_folder() -> list[dict[str, str]]:
    return [{"id": FOLDER_ID, "displayName": "Nightjar Quarantine"}]


def quarantine_action(**params: Any) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.QUARANTINE_MESSAGE,
        target="msg_1",
        params={"connector": "microsoft", "message_id": "msg_1", **params},
        risk=RiskClass.R1,
    )


def block_action(**params: Any) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.BLOCK_SENDER,
        target="bad@example.com",
        params={"connector": "microsoft", "sender": "bad@example.com", **params},
        risk=RiskClass.R2,
    )


def revoke_action(**params: Any) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.REVOKE_CREDENTIAL,
        target=MAILBOX,
        params={"connector": "microsoft", "mailbox": MAILBOX, **params},
        risk=RiskClass.R2,
    )


def predicate(check: str, **params: Any) -> VerificationPredicate:
    return VerificationPredicate(
        kind=PredicateKind.CONNECTOR_CHECK,
        description="microsoft check",
        params={"check": check, **params},
    )


# --------------------------------------------------------------------- auth
@respx.mock
async def test_the_client_credentials_grant_uses_the_app_scope(
    connector: Microsoft365Connector,
) -> None:
    route = token_route()
    folders_route(quarantine_folder())
    respx.post(f"{USER}/messages/msg_1/move").mock(
        return_value=Response(200, json={"id": "msg_2", "parentFolderId": FOLDER_ID})
    )

    await connector.execute(quarantine_action())

    form = route.calls[0].request.content.decode()
    assert "grant_type=client_credentials" in form
    assert "client_id=client-id" in form
    assert "scope=https%3A%2F%2Fgraph.microsoft.com%2F.default" in form


@respx.mock
async def test_the_access_token_is_cached_between_actions(
    connector: Microsoft365Connector,
) -> None:
    route = token_route()
    folders_route(quarantine_folder())
    respx.post(f"{USER}/messages/msg_1/move").mock(return_value=Response(200, json={"id": "msg_2"}))
    respx.post(f"{USER}/messages/msg_2/move").mock(return_value=Response(200, json={"id": "msg_3"}))

    await connector.execute(quarantine_action())
    await connector.execute(quarantine_action(message_id="msg_2"))

    assert route.call_count == 1


@respx.mock
async def test_a_refused_grant_names_the_setup_step(connector: Microsoft365Connector) -> None:
    respx.post(LOGIN).mock(
        return_value=Response(
            400,
            json={
                "error": "invalid_client",
                "error_description": "AADSTS7000215: Invalid client secret provided.",
            },
        )
    )
    with pytest.raises(ConnectorError, match="admin consent"):
        await connector.execute(quarantine_action())


@respx.mock
async def test_a_missing_access_token_is_refused(connector: Microsoft365Connector) -> None:
    respx.post(LOGIN).mock(return_value=Response(200, json={"expires_in": 3600}))
    with pytest.raises(ConnectorError, match="access_token"):
        await connector.execute(quarantine_action())


# ---------------------------------------------------------------- quarantine
@respx.mock
async def test_quarantine_moves_the_message_and_returns_the_new_id(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    folders_route(quarantine_folder())
    move = respx.post(f"{USER}/messages/msg_1/move").mock(
        return_value=Response(200, json={"id": "msg_2", "parentFolderId": FOLDER_ID})
    )

    result = await connector.execute(quarantine_action())

    assert json.loads(move.calls[0].request.content) == {"destinationId": FOLDER_ID}
    assert result["message_id"] == "msg_1"
    assert result["new_message_id"] == "msg_2"
    assert result["folder_id"] == FOLDER_ID
    assert result["parent_folder_id"] == FOLDER_ID


@respx.mock
async def test_a_missing_quarantine_folder_is_created_once(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    folders_route([])
    create = respx.post(FOLDERS).mock(
        return_value=Response(201, json={"id": FOLDER_ID, "displayName": "Nightjar Quarantine"})
    )
    respx.post(f"{USER}/messages/msg_1/move").mock(
        return_value=Response(200, json={"id": "msg_2", "parentFolderId": FOLDER_ID})
    )

    await connector.execute(quarantine_action())

    assert json.loads(create.calls[0].request.content) == {"displayName": "Nightjar Quarantine"}


async def test_quarantine_without_a_message_id_is_a_clear_error(
    connector: Microsoft365Connector,
) -> None:
    with pytest.raises(ConnectorError, match="message_id"):
        await connector.execute(quarantine_action(message_id=""))


# -------------------------------------------------------------- block sender
@respx.mock
async def test_block_sender_creates_an_inbox_rule(connector: Microsoft365Connector) -> None:
    token_route()
    folders_route(quarantine_folder())
    respx.get(RULES).mock(return_value=Response(200, json={"value": []}))
    create = respx.post(RULES).mock(
        return_value=Response(
            201, json={"id": "rule_1", "displayName": "Nightjar: bad@example.com"}
        )
    )

    result = await connector.execute(block_action())

    body = json.loads(create.calls[0].request.content)
    assert body["displayName"] == "Nightjar: bad@example.com"
    assert body["conditions"]["fromAddresses"] == [{"emailAddress": {"address": "bad@example.com"}}]
    assert body["actions"] == {"moveToFolder": FOLDER_ID, "stopProcessingRules": True}
    assert result["rule_id"] == "rule_1"
    assert result["replay"] is False


@respx.mock
async def test_block_sender_replays_an_existing_rule(connector: Microsoft365Connector) -> None:
    token_route()
    folders_route(quarantine_folder())
    respx.get(RULES).mock(
        return_value=Response(
            200,
            json={
                "value": [
                    {
                        "id": "rule_1",
                        "displayName": "Nightjar: bad@example.com",
                        "isEnabled": True,
                        "conditions": {
                            "fromAddresses": [{"emailAddress": {"address": "BAD@example.com"}}]
                        },
                    }
                ]
            },
        )
    )
    create = respx.post(RULES).mock(return_value=Response(201, json={"id": "rule_2"}))

    result = await connector.execute(block_action())

    assert create.called is False
    assert result["replay"] is True
    assert result["rule_id"] == "rule_1"


async def test_block_sender_without_a_sender_is_a_clear_error(
    connector: Microsoft365Connector,
) -> None:
    with pytest.raises(ConnectorError, match="sender"):
        await connector.execute(block_action(sender=""))


# ----------------------------------------------------------- session revoke
@respx.mock
async def test_revoking_sessions_records_the_request_time(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    revoke = respx.post(f"{USER}/revokeSignInSessions").mock(
        return_value=Response(200, json={"value": True})
    )

    result = await connector.execute(revoke_action())

    assert revoke.called is True
    assert result["user"] == MAILBOX
    assert result["revoked"] is True
    assert result["requested_at"]


@respx.mock
async def test_an_unconfirmed_revocation_is_an_error(connector: Microsoft365Connector) -> None:
    token_route()
    respx.post(f"{USER}/revokeSignInSessions").mock(
        return_value=Response(200, json={"value": False})
    )

    with pytest.raises(ConnectorError, match="did not confirm"):
        await connector.execute(revoke_action())


# --------------------------------------------------------------- resolution
async def test_more_than_one_mailbox_requires_an_explicit_one() -> None:
    connector = Microsoft365Connector(
        TENANT, "id", "secret", mailboxes=("a@example.com", "b@example.com")
    )
    with pytest.raises(ConnectorError, match=r"params\.mailbox"):
        await connector.execute(quarantine_action())


async def test_no_configured_mailbox_is_a_clear_error() -> None:
    connector = Microsoft365Connector(TENANT, "id", "secret")
    with pytest.raises(ConnectorError, match="NIGHTJAR_MANAGED_TARGETS"):
        await connector.execute(quarantine_action())


# ------------------------------------------------------------- verification
@respx.mock
async def test_the_original_id_falls_back_to_the_moved_copy(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    folders_route(quarantine_folder())
    respx.get(f"{USER}/messages/msg_1").mock(
        return_value=Response(
            404, json={"error": {"code": "ErrorItemNotFound", "message": "not found"}}
        )
    )
    respx.get(f"{USER}/messages/msg_2").mock(
        return_value=Response(200, json={"id": "msg_2", "parentFolderId": FOLDER_ID})
    )

    observed = await connector.verification_checks()["microsoft.message_folder"](
        predicate(
            "microsoft.message_folder",
            message_id="msg_1",
            new_message_id="msg_2",
        )
    )

    assert observed["passed"] is True
    assert observed["message_id"] == "msg_2"


@respx.mock
async def test_a_message_still_in_the_inbox_fails_verification(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    folders_route(quarantine_folder())
    respx.get(f"{USER}/messages/msg_1").mock(
        return_value=Response(200, json={"id": "msg_1", "parentFolderId": INBOX_ID})
    )

    observed = await connector.verification_checks()["microsoft.message_folder"](
        predicate("microsoft.message_folder", message_id="msg_1")
    )

    assert observed["passed"] is False
    assert observed["parent_folder_id"] == INBOX_ID


@respx.mock
async def test_verification_never_creates_the_quarantine_folder(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    folders_route([])
    create = respx.post(FOLDERS).mock(return_value=Response(201, json={"id": FOLDER_ID}))

    with pytest.raises(VerificationError, match="does not exist"):
        await connector.verification_checks()["microsoft.message_folder"](
            predicate("microsoft.message_folder", message_id="msg_1")
        )

    assert create.called is False


@respx.mock
async def test_an_absent_message_under_every_id_fails_verification(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    folders_route(quarantine_folder())
    respx.get(f"{USER}/messages/msg_1").mock(return_value=Response(404, json={}))
    respx.get(f"{USER}/messages/msg_2").mock(return_value=Response(404, json={}))

    observed = await connector.verification_checks()["microsoft.message_folder"](
        predicate("microsoft.message_folder", message_id="msg_1", new_message_id="msg_2")
    )

    assert observed["passed"] is False
    assert "not visible" in observed["reason"]


@respx.mock
async def test_a_present_rule_passes_and_a_missing_one_fails(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    respx.get(RULES).mock(
        side_effect=[
            Response(
                200,
                json={
                    "value": [
                        {
                            "id": "rule_1",
                            "displayName": "Nightjar: bad@example.com",
                            "isEnabled": True,
                            "conditions": {
                                "fromAddresses": [{"emailAddress": {"address": "bad@example.com"}}]
                            },
                        }
                    ]
                },
            ),
            Response(200, json={"value": []}),
        ]
    )
    check = connector.verification_checks()["microsoft.rule_present"]

    passed = await check(predicate("microsoft.rule_present", sender="bad@example.com"))
    failed = await check(predicate("microsoft.rule_present", sender="bad@example.com"))

    assert passed["passed"] is True
    assert passed["enabled"] is True
    assert failed["passed"] is False


@respx.mock
async def test_a_sign_in_inside_the_grace_window_still_passes(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    respx.get(USER).mock(
        return_value=Response(
            200,
            json={
                "id": "u1",
                "userPrincipalName": MAILBOX,
                "signInActivity": {
                    "lastSignInDateTime": "2026-01-01T10:00:30Z",
                    "lastNonInteractiveSignInDateTime": "2026-01-01T09:59:00Z",
                },
            },
        )
    )

    observed = await connector.verification_checks()["microsoft.sessions_revoked"](
        predicate(
            "microsoft.sessions_revoked",
            mailbox=MAILBOX,
            since="2026-01-01T10:00:00+00:00",
            grace_seconds=60,
        )
    )

    assert observed["passed"] is True


@respx.mock
async def test_a_sign_in_after_the_grace_window_fails_verification(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    respx.get(USER).mock(
        return_value=Response(
            200,
            json={
                "id": "u1",
                "signInActivity": {"lastSignInDateTime": "2026-01-01T10:05:00Z"},
            },
        )
    )

    observed = await connector.verification_checks()["microsoft.sessions_revoked"](
        predicate(
            "microsoft.sessions_revoked",
            mailbox=MAILBOX,
            since="2026-01-01T10:00:00+00:00",
            grace_seconds=60,
        )
    )

    assert observed["passed"] is False
    assert observed["last_sign_in"] == "2026-01-01T10:05:00+00:00"


@respx.mock
async def test_an_account_with_no_sign_in_activity_passes(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    respx.get(USER).mock(
        return_value=Response(
            200,
            json={
                "id": "u1",
                "signInActivity": {
                    "lastSignInDateTime": None,
                    "lastNonInteractiveSignInDateTime": None,
                },
            },
        )
    )

    observed = await connector.verification_checks()["microsoft.sessions_revoked"](
        predicate(
            "microsoft.sessions_revoked",
            mailbox=MAILBOX,
            since="2026-01-01T10:00:00+00:00",
        )
    )

    assert observed["passed"] is True


@respx.mock
async def test_a_missing_sign_in_activity_fails_closed(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    respx.get(USER).mock(
        return_value=Response(200, json={"id": "u1", "userPrincipalName": MAILBOX})
    )

    with pytest.raises(VerificationError, match=r"AuditLog\.Read\.All"):
        await connector.verification_checks()["microsoft.sessions_revoked"](
            predicate(
                "microsoft.sessions_revoked",
                mailbox=MAILBOX,
                since="2026-01-01T10:00:00+00:00",
            )
        )


async def test_a_session_check_without_since_is_a_verification_error(
    connector: Microsoft365Connector,
) -> None:
    with pytest.raises(VerificationError, match="since"):
        await connector.verification_checks()["microsoft.sessions_revoked"](
            predicate("microsoft.sessions_revoked", mailbox=MAILBOX)
        )


@respx.mock
async def test_a_403_during_verification_is_a_failed_predicate_not_a_crash(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    respx.get(RULES).mock(
        return_value=Response(
            403,
            json={
                "error": {
                    "code": "ErrorAccessDenied",
                    "message": "Access is denied. Check credentials and try again.",
                }
            },
        )
    )

    with pytest.raises(VerificationError, match="admin-consented"):
        await connector.verification_checks()["microsoft.rule_present"](
            predicate("microsoft.rule_present", sender="bad@example.com")
        )


async def test_a_check_without_its_identifier_is_a_verification_error(
    connector: Microsoft365Connector,
) -> None:
    with pytest.raises(VerificationError, match="message_id"):
        await connector.verification_checks()["microsoft.message_folder"](
            predicate("microsoft.message_folder")
        )


# --------------------------------------------------------------- transport
@respx.mock
async def test_rate_limiting_is_retryable_with_the_retry_after(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    respx.get(FOLDERS).mock(return_value=Response(429, headers={"Retry-After": "30"}, json={}))

    with pytest.raises(ConnectorError, match="retry after 30s") as excinfo:
        await connector.execute(quarantine_action())
    assert excinfo.value.retryable is True


@respx.mock
async def test_an_unreachable_graph_is_retryable(connector: Microsoft365Connector) -> None:
    token_route()
    respx.get(FOLDERS).mock(side_effect=httpx.ConnectError("no route to host"))

    with pytest.raises(ConnectorError, match="unavailable") as excinfo:
        await connector.execute(quarantine_action())
    assert excinfo.value.retryable is True
    # A connection that never opened cannot have applied anything.
    assert excinfo.value.maybe_applied is False


@respx.mock
async def test_a_rejected_token_is_reported(connector: Microsoft365Connector) -> None:
    token_route()
    respx.get(FOLDERS).mock(return_value=Response(401, json={}))

    with pytest.raises(ConnectorError, match="401"):
        await connector.execute(quarantine_action())


# ------------------------------------------------------------------ preflight
@respx.mock
async def test_preflight_proves_the_managed_mailbox(connector: Microsoft365Connector) -> None:
    token_route()
    respx.get(USER).mock(
        return_value=Response(200, json={"id": "u1", "userPrincipalName": MAILBOX})
    )
    report = await connector.preflight()
    assert report.verdict == "ok"
    assert report.identity == f"user:{MAILBOX}"


async def test_preflight_without_a_mailbox_is_degraded() -> None:
    report = await Microsoft365Connector(TENANT, "id", "secret").preflight()
    assert report.verdict == "degraded"
    assert "no mailboxes" in report.detail


@respx.mock
async def test_preflight_reports_a_consent_problem(connector: Microsoft365Connector) -> None:
    token_route()
    respx.get(USER).mock(
        return_value=Response(
            403,
            json={"error": {"code": "ErrorAccessDenied", "message": "no consent"}},
        )
    )
    report = await connector.preflight()
    assert report.verdict == "error"
    assert "admin-consented" in report.detail


# ---------------------------------------------------------------- read tool
@respx.mock
async def test_reading_a_message_returns_headers_as_evidence(
    connector: Microsoft365Connector,
) -> None:
    token_route()
    respx.get(f"{USER}/messages/msg_1").mock(
        return_value=Response(
            200,
            json={
                "id": "msg_1",
                "parentFolderId": INBOX_ID,
                "internetMessageId": "<abc@example.com>",
                "subject": "Payout failed",
                "receivedDateTime": "2026-01-01T09:00:00Z",
                "bodyPreview": "confirm your payout details",
                "from": {"emailAddress": {"address": "bad@example.com"}},
                "toRecipients": [{"emailAddress": {"address": MAILBOX}}],
            },
        )
    )

    evidence = await connector.collect("microsoft.message", {"message_id": "msg_1"})

    assert evidence.source == f"microsoft:{MAILBOX}/msg_1"
    assert evidence.content["from"] == "bad@example.com"
    assert evidence.content["subject"] == "Payout failed"
    assert evidence.content["to"] == [MAILBOX]


async def test_an_unknown_tool_is_refused(connector: Microsoft365Connector) -> None:
    with pytest.raises(ConnectorError, match="unknown microsoft tool"):
        await connector.collect("microsoft.other", {})


def test_capabilities_cover_mail_and_sessions(connector: Microsoft365Connector) -> None:
    capabilities = connector.capabilities()
    assert {item.action_type for item in capabilities} == {
        ActionType.QUARANTINE_MESSAGE,
        ActionType.BLOCK_SENDER,
        ActionType.REVOKE_CREDENTIAL,
    }
    assert all(item.automated for item in capabilities)


def test_the_connector_never_exposes_the_client_secret(connector: Microsoft365Connector) -> None:
    assert "client-secret" not in repr(connector)


@respx.mock
async def test_a_graph_server_error_is_retryable(connector: Microsoft365Connector) -> None:
    token_route()
    respx.get(FOLDERS).mock(return_value=Response(503, json={}))

    with pytest.raises(ConnectorError) as excinfo:
        await connector.execute(quarantine_action())
    assert excinfo.value.retryable is True
