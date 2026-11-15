from __future__ import annotations

import json

import httpx
import pytest
import respx
from httpx import Response

from nightjar.connectors.base import ConnectorError
from nightjar.connectors.render import RenderConnector
from nightjar.core.enums import ActionType, PredicateKind, RiskClass
from nightjar.core.models import ActionSpec, VerificationPredicate
from nightjar.core.verification import VerificationError

BASE = "https://api.render.com/v1"
SERVICE = "srv-cornershop"
DEPLOY = "dep-good"
COMMIT = "abc123"


@pytest.fixture
def connector() -> RenderConnector:
    return RenderConnector("rnd-key", service_id=SERVICE)


def rollback(**params: object) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        target=DEPLOY,
        params={"service": SERVICE, "target_version": DEPLOY, **params},
        risk=RiskClass.R2,
    )


def restart(**params: object) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.RESTART_SERVICE,
        target=SERVICE,
        params={"service": SERVICE, **params},
        risk=RiskClass.R2,
    )


def predicate(check: str, **params: object) -> VerificationPredicate:
    return VerificationPredicate(
        kind=PredicateKind.CONNECTOR_CHECK,
        description="render check",
        params={"check": check, **params},
    )


@respx.mock
async def test_rollback_pauses_autodeploy_then_rolls_back(connector: RenderConnector) -> None:
    respx.get(f"{BASE}/services/{SERVICE}").mock(
        return_value=Response(200, json={"id": SERVICE, "autoDeploy": "yes"})
    )
    pause = respx.patch(f"{BASE}/services/{SERVICE}").mock(
        return_value=Response(200, json={"id": SERVICE, "autoDeploy": "no"})
    )
    call = respx.post(f"{BASE}/services/{SERVICE}/rollback").mock(
        return_value=Response(200, json={"id": DEPLOY, "status": "created"})
    )

    result = await connector.execute(rollback())

    assert json.loads(pause.calls[0].request.content) == {"autoDeploy": "no"}
    assert json.loads(call.calls[0].request.content) == {"deployId": DEPLOY}
    assert result["autodeploy_paused"] is True
    assert result["previous_autodeploy"] == "yes"
    assert result["deploy_id"] == DEPLOY


@respx.mock
async def test_rollback_does_not_patch_an_already_paused_service(
    connector: RenderConnector,
) -> None:
    respx.get(f"{BASE}/services/{SERVICE}").mock(
        return_value=Response(200, json={"id": SERVICE, "autoDeploy": "no"})
    )
    pause = respx.patch(f"{BASE}/services/{SERVICE}").mock(return_value=Response(200, json={}))
    respx.post(f"{BASE}/services/{SERVICE}/rollback").mock(
        return_value=Response(200, json={"id": DEPLOY, "status": "created"})
    )

    result = await connector.execute(rollback())

    assert pause.called is False
    assert result["autodeploy_paused"] is False


@respx.mock
async def test_rollback_can_leave_autodeploy_alone_when_asked(
    connector: RenderConnector,
) -> None:
    respx.get(f"{BASE}/services/{SERVICE}").mock(
        return_value=Response(200, json={"id": SERVICE, "autoDeploy": "yes"})
    )
    pause = respx.patch(f"{BASE}/services/{SERVICE}").mock(return_value=Response(200, json={}))
    respx.post(f"{BASE}/services/{SERVICE}/rollback").mock(
        return_value=Response(200, json={"id": DEPLOY, "status": "created"})
    )

    result = await connector.execute(rollback(pause_autodeploy=False))

    assert pause.called is False
    assert result["autodeploy_paused"] is False


@respx.mock
async def test_restart_posts_to_the_restart_endpoint(connector: RenderConnector) -> None:
    route = respx.post(f"{BASE}/services/{SERVICE}/restart").mock(
        return_value=Response(200, json={"id": DEPLOY, "status": "created"})
    )
    result = await connector.execute(restart())
    assert route.called is True
    assert result["service_id"] == SERVICE


async def test_a_service_is_required() -> None:
    with pytest.raises(ConnectorError, match="NIGHTJAR_RENDER_SERVICE_ID"):
        await RenderConnector("rnd-key").execute(restart(service=""))


@respx.mock
async def test_a_rate_limit_names_retry_after(connector: RenderConnector) -> None:
    respx.get(f"{BASE}/services/{SERVICE}").mock(
        return_value=Response(429, headers={"retry-after": "4"}, json={})
    )
    with pytest.raises(ConnectorError, match="retry after 4s") as excinfo:
        await connector.execute(rollback())
    assert excinfo.value.retryable is True


@respx.mock
async def test_deploy_live_requires_status_and_commit(connector: RenderConnector) -> None:
    respx.get(f"{BASE}/services/{SERVICE}/deploys/{DEPLOY}").mock(
        side_effect=[
            Response(200, json={"status": "live", "commit": {"id": COMMIT}}),
            Response(200, json={"status": "build_in_progress", "commit": {"id": COMMIT}}),
            Response(200, json={"status": "live", "commit": {"id": "other"}}),
        ]
    )
    check = connector.verification_checks()["render.deploy_live"]

    assert (
        await check(
            predicate(
                "render.deploy_live",
                service_id=SERVICE,
                deploy_id=DEPLOY,
                commit_id=COMMIT,
            )
        )
    )["passed"]
    assert not (
        await check(
            predicate(
                "render.deploy_live",
                service_id=SERVICE,
                deploy_id=DEPLOY,
                commit_id=COMMIT,
            )
        )
    )["passed"]
    assert not (
        await check(
            predicate(
                "render.deploy_live",
                service_id=SERVICE,
                deploy_id=DEPLOY,
                commit_id=COMMIT,
            )
        )
    )["passed"]


async def test_deploy_live_requires_identifiers(connector: RenderConnector) -> None:
    with pytest.raises(VerificationError, match="service_id and deploy_id"):
        await connector.verification_checks()["render.deploy_live"](predicate("render.deploy_live"))


@respx.mock
async def test_autodeploy_disabled_reads_the_service(connector: RenderConnector) -> None:
    respx.get(f"{BASE}/services/{SERVICE}").mock(
        side_effect=[
            Response(200, json={"autoDeploy": "no"}),
            Response(200, json={"autoDeploy": "yes"}),
        ]
    )
    check = connector.verification_checks()["render.autodeploy_disabled"]
    assert (await check(predicate("render.autodeploy_disabled", service_id=SERVICE)))["passed"]
    assert not (await check(predicate("render.autodeploy_disabled", service_id=SERVICE)))["passed"]


@respx.mock
async def test_service_not_suspended_reads_the_service(connector: RenderConnector) -> None:
    respx.get(f"{BASE}/services/{SERVICE}").mock(
        side_effect=[
            Response(200, json={"suspended": "not_suspended"}),
            Response(200, json={"suspended": "suspended"}),
        ]
    )
    check = connector.verification_checks()["render.service_not_suspended"]
    assert (await check(predicate("render.service_not_suspended", service_id=SERVICE)))["passed"]
    assert not (await check(predicate("render.service_not_suspended", service_id=SERVICE)))[
        "passed"
    ]


@respx.mock
async def test_service_evidence_includes_autodeploy_state(connector: RenderConnector) -> None:
    respx.get(f"{BASE}/services/{SERVICE}").mock(
        return_value=Response(
            200,
            json={
                "id": SERVICE,
                "name": "cornershop",
                "type": "web_service",
                "autoDeploy": "no",
                "suspended": "not_suspended",
                "serviceDetails": {"url": "https://cornershop.onrender.com"},
            },
        )
    )
    evidence = await connector.collect("render.service", {})
    assert evidence.content["auto_deploy"] == "no"
    assert evidence.content["url"] == "https://cornershop.onrender.com"


@respx.mock
async def test_deploy_list_handles_the_wrapped_shape(connector: RenderConnector) -> None:
    respx.get(f"{BASE}/services/{SERVICE}/deploys").mock(
        return_value=Response(
            200,
            json=[
                {
                    "deploy": {
                        "id": DEPLOY,
                        "status": "live",
                        "commit": {"id": COMMIT},
                        "createdAt": "2026-11-01T00:00:00Z",
                    },
                    "cursor": "c1",
                }
            ],
        )
    )
    evidence = await connector.collect("render.deploys", {})
    assert evidence.content["deploys"][0]["id"] == DEPLOY


@respx.mock
async def test_preflight_checks_the_configured_service(connector: RenderConnector) -> None:
    respx.get(f"{BASE}/services/{SERVICE}").mock(
        return_value=Response(200, json={"id": SERVICE, "name": "cornershop", "autoDeploy": "no"})
    )
    report = await connector.preflight()
    assert report.verdict == "ok"
    assert report.identity == "service:cornershop"
    assert report.detail == "autodeploy is off"


@respx.mock
async def test_preflight_without_a_service_lists_services() -> None:
    connector = RenderConnector("rnd-key")
    respx.get(f"{BASE}/services").mock(return_value=Response(200, json=[]))
    report = await connector.preflight()
    assert report.verdict == "ok"
    assert "NIGHTJAR_RENDER_SERVICE_ID" in report.detail


@respx.mock
async def test_preflight_reports_a_rejected_key() -> None:
    connector = RenderConnector("rnd-key")
    respx.get(f"{BASE}/services").mock(return_value=Response(401, json={}))
    report = await connector.preflight()
    assert report.verdict == "error"
    assert "rejected" in report.detail


@respx.mock
async def test_preflight_reports_an_unreachable_api() -> None:
    connector = RenderConnector("rnd-key")
    respx.get(f"{BASE}/services").mock(side_effect=httpx.ConnectError("dns"))
    report = await connector.preflight()
    assert report.verdict == "error"
    assert "unavailable" in report.detail


def test_capabilities_cover_rollback_and_restart(connector: RenderConnector) -> None:
    assert {item.action_type for item in connector.capabilities()} == {
        ActionType.ROLLBACK_DEPLOYMENT,
        ActionType.RESTART_SERVICE,
    }
