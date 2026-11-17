from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from nightjar.connectors.base import ConnectorError
from nightjar.connectors.vercel import VercelConnector
from nightjar.core.enums import ActionType, PredicateKind, RiskClass
from nightjar.core.models import ActionSpec, VerificationPredicate
from nightjar.core.verification import VerificationError

BASE = "https://api.vercel.com"
PROJECT = "prj_cornershop"
DEPLOYMENT = "dpl_good"
CURRENT = "dpl_bad"
DOMAIN = "shop.example"


@pytest.fixture
def connector() -> VercelConnector:
    return VercelConnector("vercel-token", project=PROJECT)


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
        description="vercel check",
        params={"check": check, **params},
    )


@respx.mock
async def test_rollback_validates_ready_then_promotes(connector: VercelConnector) -> None:
    lookup = respx.get(f"{BASE}/v13/deployments/{DEPLOYMENT}").mock(
        return_value=Response(200, json={"id": DEPLOYMENT, "readyState": "READY"})
    )
    call = respx.post(f"{BASE}/v1/projects/{PROJECT}/rollback/{DEPLOYMENT}").mock(
        return_value=Response(200, json={"id": DEPLOYMENT, "readyState": "READY"})
    )

    result = await connector.execute(rollback())

    assert lookup.called is True
    assert call.called is True
    assert result["deployment_id"] == DEPLOYMENT
    assert result["ready_state"] == "READY"


@respx.mock
async def test_rollback_refuses_a_deployment_that_is_not_ready(
    connector: VercelConnector,
) -> None:
    respx.get(f"{BASE}/v13/deployments/{DEPLOYMENT}").mock(
        return_value=Response(200, json={"id": DEPLOYMENT, "readyState": "ERROR"})
    )
    call = respx.post(f"{BASE}/v1/projects/{PROJECT}/rollback/{DEPLOYMENT}").mock(
        return_value=Response(200, json={})
    )

    with pytest.raises(ConnectorError, match="not READY"):
        await connector.execute(rollback())
    assert call.called is False


async def test_rollback_without_a_project_is_actionable() -> None:
    action = rollback(service="")
    with pytest.raises(ConnectorError, match="NIGHTJAR_VERCEL_PROJECT_ID"):
        await VercelConnector("vercel-token").execute(action)


def test_the_plan_project_wins_over_configuration(connector: VercelConnector) -> None:
    assert connector._project_id(rollback(service="other")) == "other"
    assert connector._project_id(rollback()) == PROJECT


@respx.mock
async def test_a_rate_limit_names_retry_after(connector: VercelConnector) -> None:
    respx.get(f"{BASE}/v13/deployments/{DEPLOYMENT}").mock(
        return_value=Response(429, headers={"retry-after": "7"}, json={})
    )
    with pytest.raises(ConnectorError, match="retry after 7s") as excinfo:
        await connector.execute(rollback())
    assert excinfo.value.retryable is True
    assert excinfo.value.retry_after == 7.0


@respx.mock
async def test_the_team_scope_is_sent_on_every_request() -> None:
    connector = VercelConnector("vercel-token", team_id="team_1", project=PROJECT)
    route = respx.get(f"{BASE}/v13/deployments/{DEPLOYMENT}").mock(
        return_value=Response(200, json={"id": DEPLOYMENT, "readyState": "READY"})
    )
    respx.post(f"{BASE}/v1/projects/{PROJECT}/rollback/{DEPLOYMENT}").mock(
        return_value=Response(200, json={})
    )
    await connector.execute(rollback())
    assert route.calls[0].request.url.params["teamId"] == "team_1"


@respx.mock
async def test_deployment_ready_requires_ready_and_production(connector: VercelConnector) -> None:
    respx.get(f"{BASE}/v13/deployments/{DEPLOYMENT}").mock(
        side_effect=[
            Response(200, json={"readyState": "READY", "target": "production"}),
            Response(200, json={"readyState": "READY", "target": "preview"}),
            Response(200, json={"readyState": "BUILDING", "target": "production"}),
        ]
    )
    check = connector.verification_checks()["vercel.deployment_ready"]

    assert (await check(predicate("vercel.deployment_ready", deployment_id=DEPLOYMENT)))["passed"]
    assert not (await check(predicate("vercel.deployment_ready", deployment_id=DEPLOYMENT)))[
        "passed"
    ]
    assert not (await check(predicate("vercel.deployment_ready", deployment_id=DEPLOYMENT)))[
        "passed"
    ]


@respx.mock
async def test_the_alias_check_requires_the_production_domain(
    connector: VercelConnector,
) -> None:
    respx.get(f"{BASE}/v2/deployments/{DEPLOYMENT}/aliases").mock(
        side_effect=[
            Response(200, json={"aliases": [{"alias": DOMAIN}]}),
            Response(200, json={"aliases": [{"alias": "old.example"}]}),
        ]
    )
    check = connector.verification_checks()["vercel.alias_present"]

    passed = await check(predicate("vercel.alias_present", deployment_id=DEPLOYMENT, domain=DOMAIN))
    failed = await check(predicate("vercel.alias_present", deployment_id=DEPLOYMENT, domain=DOMAIN))

    assert passed["passed"] is True
    assert failed["passed"] is False
    assert failed["aliases"] == ["old.example"]


async def test_the_alias_check_requires_a_domain(connector: VercelConnector) -> None:
    with pytest.raises(VerificationError, match="domain"):
        await connector.verification_checks()["vercel.alias_present"](
            predicate("vercel.alias_present", deployment_id=DEPLOYMENT)
        )


@respx.mock
async def test_deployments_are_returned_as_evidence(connector: VercelConnector) -> None:
    respx.get(f"{BASE}/v6/deployments").mock(
        return_value=Response(
            200,
            json={
                "deployments": [
                    {
                        "uid": DEPLOYMENT,
                        "url": "cornershop.vercel.app",
                        "readyState": "READY",
                        "target": "production",
                        "createdAt": 1763000000000,
                    }
                ]
            },
        )
    )
    evidence = await connector.collect("vercel.deployments", {})
    assert evidence.content["deployments"][0]["id"] == DEPLOYMENT
    assert evidence.source == f"vercel:project/{PROJECT}"


@respx.mock
async def test_preflight_proves_the_token_and_team(connector: VercelConnector) -> None:
    respx.get(f"{BASE}/v2/user").mock(
        return_value=Response(200, json={"user": {"username": "nightjar-bot"}})
    )
    report = await connector.preflight()
    assert report.verdict == "ok"
    assert report.identity == "user:nightjar-bot"
    assert report.detail == "personal scope"


@respx.mock
async def test_preflight_reports_a_rejected_token(connector: VercelConnector) -> None:
    respx.get(f"{BASE}/v2/user").mock(return_value=Response(403, json={}))
    report = await connector.preflight()
    assert report.verdict == "error"
    assert "rejected" in report.detail


@respx.mock
async def test_preflight_reports_an_unreachable_api(connector: VercelConnector) -> None:
    respx.get(f"{BASE}/v2/user").mock(side_effect=httpx.ConnectError("dns"))
    report = await connector.preflight()
    assert report.verdict == "error"
    assert "unavailable" in report.detail


def test_capabilities_cover_rollback(connector: VercelConnector) -> None:
    assert [item.action_type for item in connector.capabilities()] == [
        ActionType.ROLLBACK_DEPLOYMENT
    ]
