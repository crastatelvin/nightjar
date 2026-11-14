from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
import respx
from httpx import Response

from nightjar.connectors.cloudflare import CloudflareConnector
from nightjar.connectors.github import GitHubConnector
from nightjar.connectors.webhook import WebhookConnector

GITHUB = "https://api.github.com"
CLOUDFLARE = "https://api.cloudflare.com/client/v4"


# ------------------------------------------------------------------- github
async def test_github_without_a_token_is_revocation_only() -> None:
    report = await GitHubConnector(None).preflight()
    assert report.verdict == "ok"
    assert "revocation only" in report.identity
    assert [item.action_type.value for item in GitHubConnector(None).capabilities()] == [
        "revoke_credential"
    ]


@respx.mock
async def test_github_token_is_proven_by_reading_the_user() -> None:
    respx.get(f"{GITHUB}/user").mock(
        return_value=Response(
            200, json={"login": "nightjar-bot"}, headers={"x-oauth-scopes": "repo"}
        )
    )
    report = await GitHubConnector("ghp_x").preflight()
    assert report.verdict == "ok"
    assert report.identity == "token:nightjar-bot"
    assert "repo" in report.detail


@respx.mock
async def test_a_rejected_github_token_is_an_error_not_a_degradation() -> None:
    respx.get(f"{GITHUB}/user").mock(return_value=Response(401, json={}))
    report = await GitHubConnector("ghp_x").preflight()
    assert report.verdict == "error"
    assert "rejected" in report.detail


@respx.mock
async def test_a_forbidden_github_token_reports_the_missing_scope() -> None:
    respx.get(f"{GITHUB}/user").mock(return_value=Response(403, json={"message": "forbidden"}))
    report = await GitHubConnector("ghp_x").preflight()
    assert report.verdict == "degraded"
    assert report.missing_scopes == ("issues:write",)


@respx.mock
async def test_an_unreachable_github_is_an_error_without_a_traceback() -> None:
    respx.get(f"{GITHUB}/user").mock(side_effect=httpx.ConnectError("dns"))
    report = await GitHubConnector("ghp_x").preflight()
    assert report.verdict == "error"
    assert "unreachable" in report.detail


# --------------------------------------------------------------- cloudflare
@respx.mock
async def test_an_active_cloudflare_token_is_ok() -> None:
    respx.get(f"{CLOUDFLARE}/user/tokens/verify").mock(
        return_value=Response(
            200, json={"success": True, "result": {"id": "abcdef123456", "status": "active"}}
        )
    )
    report = await CloudflareConnector("cf").preflight()
    assert report.verdict == "ok"
    assert report.identity == "token:abcdef12…"


@respx.mock
async def test_a_rejected_cloudflare_token_is_an_error() -> None:
    respx.get(f"{CLOUDFLARE}/user/tokens/verify").mock(return_value=Response(401, json={}))
    report = await CloudflareConnector("cf").preflight()
    assert report.verdict == "error"
    assert "rejected" in report.detail


@respx.mock
async def test_a_cloudflare_token_that_is_not_active_is_degraded() -> None:
    respx.get(f"{CLOUDFLARE}/user/tokens/verify").mock(
        return_value=Response(200, json={"success": True, "result": {"status": "disabled"}})
    )
    report = await CloudflareConnector("cf").preflight()
    assert report.verdict == "degraded"
    assert "disabled" in report.detail


@respx.mock
async def test_cloudflare_unsuccessful_verification_names_the_error() -> None:
    respx.get(f"{CLOUDFLARE}/user/tokens/verify").mock(
        return_value=Response(200, json={"success": False, "errors": [{"message": "bad token"}]})
    )
    report = await CloudflareConnector("cf").preflight()
    assert report.verdict == "error"
    assert "bad token" in report.detail


# ------------------------------------------------------------------ webhook
async def test_a_shallow_webhook_check_does_not_send_anything() -> None:
    report = await WebhookConnector("https://hooks.example.com/nightjar").preflight()
    assert report.verdict == "ok"
    assert "not" not in report.detail.lower()
    assert "--deep" in report.detail


async def test_a_plain_http_webhook_is_degraded() -> None:
    report = await WebhookConnector("http://hooks.example.com/nightjar").preflight()
    assert report.verdict == "degraded"
    assert "HTTPS" in report.detail


@respx.mock
async def test_a_deep_webhook_check_sends_a_signed_ping() -> None:
    route = respx.post("https://hooks.example.com/nightjar").mock(return_value=Response(204))
    connector = WebhookConnector("https://hooks.example.com/nightjar", secret="s3cret")
    report = await connector.preflight(deep=True)

    assert report.verdict == "ok"
    assert "signed ping 204" in report.detail
    request = route.calls[0].request
    header = request.headers["x-nightjar-signature"]
    timestamp, signature = header.removeprefix("t=").split(",v1=")
    expected = hmac.new(
        b"s3cret", timestamp.encode() + b"." + request.content, hashlib.sha256
    ).hexdigest()
    assert signature == expected
    assert json.loads(request.content)["event"] == "nightjar.doctor"
    assert "s3cret" not in request.content.decode()


@respx.mock
async def test_a_deep_webhook_check_reports_a_failure() -> None:
    respx.post("https://hooks.example.com/nightjar").mock(return_value=Response(500))
    report = await WebhookConnector("https://hooks.example.com/nightjar").preflight(deep=True)
    assert report.verdict == "error"
    assert "500" in report.detail


@pytest.mark.parametrize("secret", [None, "s3cret"])
async def test_notifications_are_signed_only_when_a_secret_is_set(secret: str | None) -> None:
    from nightjar.core.enums import ActionType, RiskClass
    from nightjar.core.models import ActionSpec

    with respx.mock:
        route = respx.post("https://hooks.example.com/nightjar").mock(return_value=Response(202))
        connector = WebhookConnector("https://hooks.example.com/nightjar", secret=secret)
        result = await connector.execute(
            ActionSpec(
                action_type=ActionType.NOTIFY,
                target="webhook",
                params={"message": "hello", "incident_id": "inc-1"},
                risk=RiskClass.R0,
            )
        )
    assert result["signed"] is (secret is not None)
    assert ("x-nightjar-signature" in route.calls[0].request.headers) is (secret is not None)
