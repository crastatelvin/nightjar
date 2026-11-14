from __future__ import annotations

import json

import httpx
import pytest
import respx
from httpx import Response

from nightjar.connectors.base import ConnectorError
from nightjar.connectors.cloudflare import CloudflareConnector
from nightjar.core.enums import ActionType, PredicateKind, RiskClass
from nightjar.core.models import ActionSpec, VerificationPredicate
from nightjar.core.verification import VerificationError

BASE = "https://api.cloudflare.com/client/v4"
ZONE = "zone-abc"
ACCOUNT = "acct-123"
PROJECT = "cornershop"
DEPLOYMENT = "dep-good"
CURRENT = "dep-bad"


@pytest.fixture
def connector() -> CloudflareConnector:
    return CloudflareConnector("cf-token", account_id=ACCOUNT, pages_project=PROJECT)


def ok(result: object) -> Response:
    return Response(200, json={"success": True, "result": result})


def purge(**params: object) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.PURGE_CACHE,
        target=ZONE,
        params={"zone": ZONE, "urls": ["https://shop.example/checkout"], **params},
        risk=RiskClass.R1,
    )


def under_attack(**params: object) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.ENABLE_UNDER_ATTACK,
        target=ZONE,
        params={"zone": ZONE, **params},
        risk=RiskClass.R2,
    )


def rollback(**params: object) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        target=DEPLOYMENT,
        params={"service": PROJECT, "target_version": DEPLOYMENT, **params},
        risk=RiskClass.R2,
    )


def predicate(check: str, **params: object) -> VerificationPredicate:
    return VerificationPredicate(
        kind=PredicateKind.CONNECTOR_CHECK,
        description="cloudflare check",
        params={"check": check, **params},
    )


# ------------------------------------------------------------------- cache
@respx.mock
async def test_purge_sends_the_urls_and_reports_the_zone(
    connector: CloudflareConnector,
) -> None:
    route = respx.post(f"{BASE}/zones/{ZONE}/purge_cache").mock(return_value=ok({"id": "p1"}))

    result = await connector.execute(purge())

    assert json.loads(route.calls[0].request.content) == {
        "files": ["https://shop.example/checkout"]
    }
    assert route.calls[0].request.headers["authorization"] == "Bearer cf-token"
    assert result["zone"] == ZONE


@respx.mock
async def test_purge_without_urls_is_refused_before_the_call(
    connector: CloudflareConnector,
) -> None:
    route = respx.post(f"{BASE}/zones/{ZONE}/purge_cache").mock(return_value=ok({}))
    with pytest.raises(ConnectorError, match="non-empty list of urls"):
        await connector.execute(purge(urls=[]))
    assert route.called is False


@respx.mock
async def test_a_rejected_purge_names_the_cloudflare_error(
    connector: CloudflareConnector,
) -> None:
    respx.post(f"{BASE}/zones/{ZONE}/purge_cache").mock(
        return_value=Response(200, json={"success": False, "errors": [{"message": "bad"}]})
    )
    with pytest.raises(ConnectorError, match="rejected"):
        await connector.execute(purge())


# ------------------------------------------------------------- under attack
@respx.mock
async def test_under_attack_sets_the_security_level(connector: CloudflareConnector) -> None:
    route = respx.patch(f"{BASE}/zones/{ZONE}/settings/security_level").mock(
        return_value=ok({"id": "security_level", "value": "under_attack"})
    )
    result = await connector.execute(under_attack())
    assert json.loads(route.calls[0].request.content) == {"value": "under_attack"}
    assert result["security_level"] == "under_attack"


@respx.mock
async def test_the_security_level_check_compares_the_live_value(
    connector: CloudflareConnector,
) -> None:
    route = respx.get(f"{BASE}/zones/{ZONE}/settings/security_level").mock(
        side_effect=[ok({"value": "under_attack"}), ok({"value": "medium"})]
    )
    check = connector.verification_checks()["cloudflare.security_level"]

    passed = await check(predicate("cloudflare.security_level", zone_id=ZONE))
    failed = await check(predicate("cloudflare.security_level", zone_id=ZONE))

    assert route.call_count == 2
    assert passed["passed"] is True
    assert failed["passed"] is False
    assert failed["current"] == "medium"


async def test_the_security_level_check_requires_a_zone(
    connector: CloudflareConnector,
) -> None:
    with pytest.raises(VerificationError, match="zone_id"):
        await connector.verification_checks()["cloudflare.security_level"](
            predicate("cloudflare.security_level")
        )


# ---------------------------------------------------------------- rollback
@respx.mock
async def test_rollback_validates_the_target_then_posts(connector: CloudflareConnector) -> None:
    detail = respx.get(
        f"{BASE}/accounts/{ACCOUNT}/pages/projects/{PROJECT}/deployments/{DEPLOYMENT}"
    ).mock(
        return_value=ok(
            {
                "id": DEPLOYMENT,
                "environment": "production",
                "latest_stage": {"status": "success"},
            }
        )
    )
    call = respx.post(
        f"{BASE}/accounts/{ACCOUNT}/pages/projects/{PROJECT}/deployments/{DEPLOYMENT}/rollback"
    ).mock(return_value=ok({"id": DEPLOYMENT, "environment": "production"}))

    result = await connector.execute(rollback())

    assert detail.called is True
    assert call.called is True
    assert result["project"] == PROJECT
    assert result["deployment_id"] == DEPLOYMENT


@respx.mock
async def test_rollback_refuses_a_deployment_that_did_not_succeed(
    connector: CloudflareConnector,
) -> None:
    respx.get(f"{BASE}/accounts/{ACCOUNT}/pages/projects/{PROJECT}/deployments/{DEPLOYMENT}").mock(
        return_value=ok(
            {"id": DEPLOYMENT, "environment": "production", "latest_stage": {"status": "failure"}}
        )
    )
    call = respx.post(
        f"{BASE}/accounts/{ACCOUNT}/pages/projects/{PROJECT}/deployments/{DEPLOYMENT}/rollback"
    ).mock(return_value=ok({}))

    with pytest.raises(ConnectorError, match="not a successful deployment"):
        await connector.execute(rollback())
    assert call.called is False


@respx.mock
async def test_rollback_refuses_a_preview_deployment(connector: CloudflareConnector) -> None:
    respx.get(f"{BASE}/accounts/{ACCOUNT}/pages/projects/{PROJECT}/deployments/{DEPLOYMENT}").mock(
        return_value=ok(
            {"id": DEPLOYMENT, "environment": "preview", "latest_stage": {"status": "success"}}
        )
    )
    with pytest.raises(ConnectorError, match="not production"):
        await connector.execute(rollback())


async def test_rollback_without_an_account_is_actionable(connector: CloudflareConnector) -> None:
    bare = CloudflareConnector("cf-token")
    with pytest.raises(ConnectorError, match="NIGHTJAR_CLOUDFLARE_ACCOUNT_ID"):
        await bare.execute(rollback())


def test_the_plan_value_wins_over_the_configured_project(connector: CloudflareConnector) -> None:
    """Policy validated the project named in the plan, so the connector must use it."""
    assert connector._project(rollback(service="other-project")) == "other-project"
    assert connector._project(rollback()) == PROJECT


@respx.mock
async def test_the_production_check_compares_the_live_deployment(
    connector: CloudflareConnector,
) -> None:
    respx.get(f"{BASE}/accounts/{ACCOUNT}/pages/projects/{PROJECT}/deployments").mock(
        side_effect=[
            ok([{"id": DEPLOYMENT}]),
            ok([{"id": CURRENT}]),
        ]
    )
    check = connector.verification_checks()["cloudflare.pages_production"]

    passed = await check(
        predicate("cloudflare.pages_production", project=PROJECT, deployment_id=DEPLOYMENT)
    )
    failed = await check(
        predicate("cloudflare.pages_production", project=PROJECT, deployment_id=DEPLOYMENT)
    )

    assert passed["passed"] is True
    assert failed["passed"] is False
    assert failed["current"] == CURRENT


async def test_the_production_check_requires_a_deployment(
    connector: CloudflareConnector,
) -> None:
    with pytest.raises(VerificationError, match="deployment_id"):
        await connector.verification_checks()["cloudflare.pages_production"](
            predicate("cloudflare.pages_production", project=PROJECT)
        )


# -------------------------------------------------------------- read tools
@respx.mock
async def test_deployments_are_returned_as_evidence(connector: CloudflareConnector) -> None:
    respx.get(f"{BASE}/accounts/{ACCOUNT}/pages/projects/{PROJECT}/deployments").mock(
        return_value=ok(
            [
                {
                    "id": DEPLOYMENT,
                    "environment": "production",
                    "latest_stage": {"status": "success"},
                    "created_on": "2026-11-01T00:00:00Z",
                    "url": "https://cornershop.pages.dev",
                }
            ]
        )
    )
    evidence = await connector.collect("cloudflare.deployments", {})
    assert evidence.content["deployments"][0]["id"] == DEPLOYMENT
    assert evidence.source == f"cloudflare:pages/{PROJECT}"


async def test_deployments_without_configuration_are_actionable() -> None:
    with pytest.raises(ConnectorError, match="requires account_id and project"):
        await CloudflareConnector("cf-token").collect("cloudflare.deployments", {})


# -------------------------------------------------------------- preflight
@respx.mock
async def test_preflight_reports_an_unreachable_api_without_a_traceback(
    connector: CloudflareConnector,
) -> None:
    respx.get(f"{BASE}/user/tokens/verify").mock(side_effect=httpx.ConnectError("dns"))
    report = await connector.preflight()
    assert report.verdict == "error"
    assert "unavailable" in report.detail


def test_capabilities_include_pages_rollback(connector: CloudflareConnector) -> None:
    actions = {item.action_type for item in connector.capabilities()}
    assert actions == {
        ActionType.PURGE_CACHE,
        ActionType.ENABLE_UNDER_ATTACK,
        ActionType.ROLLBACK_DEPLOYMENT,
    }
