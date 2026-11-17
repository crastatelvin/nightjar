from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from nightjar.connectors.base import ConnectorError
from nightjar.connectors.fly import FlyConnector
from nightjar.core.enums import ActionType, PredicateKind, RiskClass
from nightjar.core.models import ActionSpec, VerificationPredicate
from nightjar.core.verification import VerificationError

BASE = "https://api.machines.dev"
APP = "cornershop"
MACHINE = "148ed9c0a4e028"
REQUESTED_AT = "2026-11-15T12:00:00+00:00"


@pytest.fixture
def connector() -> FlyConnector:
    return FlyConnector("fly-token", app=APP)


def restart(**params: object) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.RESTART_SERVICE,
        target=MACHINE,
        params={"app": APP, "machine_id": MACHINE, **params},
        risk=RiskClass.R2,
    )


def predicate(check: str, **params: object) -> VerificationPredicate:
    return VerificationPredicate(
        kind=PredicateKind.CONNECTOR_CHECK,
        description="fly check",
        params={"check": check, **params},
    )


@respx.mock
async def test_restart_reads_the_machine_then_posts_with_signal_and_timeout(
    connector: FlyConnector,
) -> None:
    lookup = respx.get(f"{BASE}/v1/apps/{APP}/machines/{MACHINE}").mock(
        return_value=Response(200, json={"id": MACHINE, "state": "started"})
    )
    call = respx.post(f"{BASE}/v1/apps/{APP}/machines/{MACHINE}/restart").mock(
        return_value=Response(200, json={})
    )

    result = await connector.execute(restart(timeout="45s", signal="SIGINT"))

    assert lookup.called is True
    assert call.called is True
    assert call.calls[0].request.url.params["timeout"] == "45s"
    assert call.calls[0].request.url.params["signal"] == "SIGINT"
    assert result["app"] == APP
    assert result["machine_id"] == MACHINE
    assert result["state_before"] == "started"
    assert result["requested_at"]


@respx.mock
async def test_restart_defaults_to_sigterm_and_thirty_seconds(connector: FlyConnector) -> None:
    respx.get(f"{BASE}/v1/apps/{APP}/machines/{MACHINE}").mock(
        return_value=Response(200, json={"id": MACHINE, "state": "started"})
    )
    call = respx.post(f"{BASE}/v1/apps/{APP}/machines/{MACHINE}/restart").mock(
        return_value=Response(200, json={})
    )

    await connector.execute(restart())

    assert call.calls[0].request.url.params["timeout"] == "30s"
    assert call.calls[0].request.url.params["signal"] == "SIGTERM"


async def test_restart_requires_a_machine_id(connector: FlyConnector) -> None:
    with pytest.raises(ConnectorError, match="machine_id"):
        await connector.execute(restart(machine_id=""))


async def test_restart_requires_an_app() -> None:
    with pytest.raises(ConnectorError, match="NIGHTJAR_FLY_APP"):
        await FlyConnector("fly-token").execute(restart(app=""))


@respx.mock
async def test_a_missing_machine_is_reported_before_the_restart(
    connector: FlyConnector,
) -> None:
    respx.get(f"{BASE}/v1/apps/{APP}/machines/{MACHINE}").mock(return_value=Response(404, json={}))
    call = respx.post(f"{BASE}/v1/apps/{APP}/machines/{MACHINE}/restart").mock(
        return_value=Response(200, json={})
    )

    with pytest.raises(ConnectorError, match="not found"):
        await connector.execute(restart())
    assert call.called is False


@respx.mock
async def test_a_rate_limit_names_retry_after(connector: FlyConnector) -> None:
    respx.get(f"{BASE}/v1/apps/{APP}/machines/{MACHINE}").mock(
        return_value=Response(429, headers={"retry-after": "9"}, json={})
    )
    with pytest.raises(ConnectorError, match="retry after 9s") as excinfo:
        await connector.execute(restart())
    assert excinfo.value.retryable is True
    assert excinfo.value.retry_after == 9.0


@respx.mock
async def test_a_rejected_token_says_so(connector: FlyConnector) -> None:
    respx.get(f"{BASE}/v1/apps/{APP}/machines/{MACHINE}").mock(return_value=Response(403, json={}))
    with pytest.raises(ConnectorError, match="invalid or lacks access"):
        await connector.execute(restart())


@respx.mock
async def test_machine_started_requires_started_and_a_newer_timestamp(
    connector: FlyConnector,
) -> None:
    respx.get(f"{BASE}/v1/apps/{APP}/machines/{MACHINE}").mock(
        side_effect=[
            Response(200, json={"state": "started", "updated_at": "2026-11-15T12:00:05Z"}),
            Response(200, json={"state": "restarting", "updated_at": "2026-11-15T12:00:05Z"}),
            Response(200, json={"state": "started", "updated_at": "2026-11-15T11:59:00Z"}),
        ]
    )
    check = connector.verification_checks()["fly.machine_started"]
    call = predicate("fly.machine_started", app=APP, machine_id=MACHINE, since=REQUESTED_AT)

    passed = await check(call)
    still_restarting = await check(call)
    stale = await check(call)

    assert passed["passed"] is True
    assert still_restarting["passed"] is False
    assert stale["passed"] is False
    assert stale["since"] == REQUESTED_AT


@respx.mock
async def test_machine_started_without_since_only_requires_state(
    connector: FlyConnector,
) -> None:
    respx.get(f"{BASE}/v1/apps/{APP}/machines/{MACHINE}").mock(
        return_value=Response(200, json={"state": "started", "updated_at": "2020-01-01T00:00:00Z"})
    )
    result = await connector.verification_checks()["fly.machine_started"](
        predicate("fly.machine_started", app=APP, machine_id=MACHINE)
    )
    assert result["passed"] is True
    assert result["since"] is None


async def test_machine_started_requires_identifiers(connector: FlyConnector) -> None:
    with pytest.raises(VerificationError, match="app and machine_id"):
        await connector.verification_checks()["fly.machine_started"](
            predicate("fly.machine_started")
        )


@respx.mock
async def test_machines_are_returned_as_evidence(connector: FlyConnector) -> None:
    respx.get(f"{BASE}/v1/apps/{APP}/machines").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": MACHINE,
                    "state": "started",
                    "region": "iad",
                    "instance_id": "01J",
                    "updated_at": "2026-11-15T12:00:00Z",
                }
            ],
        )
    )
    evidence = await connector.collect("fly.machines", {})
    assert evidence.content["machines"][0]["id"] == MACHINE
    assert evidence.content["machines"][0]["state"] == "started"
    assert evidence.source == f"fly:app/{APP}"


@respx.mock
async def test_preflight_checks_the_configured_app(connector: FlyConnector) -> None:
    respx.get(f"{BASE}/v1/apps/{APP}/machines").mock(
        return_value=Response(
            200,
            json=[{"id": MACHINE, "state": "started"}, {"id": "other", "state": "stopped"}],
        )
    )
    report = await connector.preflight()
    assert report.verdict == "ok"
    assert report.identity == f"app:{APP}"
    assert "2 machine(s)" in report.detail


@respx.mock
async def test_preflight_without_an_app_lists_apps() -> None:
    connector = FlyConnector("fly-token")
    respx.get(f"{BASE}/v1/apps").mock(return_value=Response(200, json=[{"name": APP}]))
    report = await connector.preflight()
    assert report.verdict == "ok"
    assert "NIGHTJAR_FLY_APP" in report.detail


@respx.mock
async def test_preflight_reports_a_rejected_token() -> None:
    connector = FlyConnector("fly-token")
    respx.get(f"{BASE}/v1/apps").mock(return_value=Response(401, json={}))
    report = await connector.preflight()
    assert report.verdict == "error"
    assert "rejected" in report.detail


@respx.mock
async def test_preflight_reports_an_unreachable_api() -> None:
    connector = FlyConnector("fly-token")
    respx.get(f"{BASE}/v1/apps").mock(side_effect=httpx.ConnectError("dns"))
    report = await connector.preflight()
    assert report.verdict == "error"
    assert "unavailable" in report.detail


def test_capabilities_cover_restart(connector: FlyConnector) -> None:
    assert [item.action_type for item in connector.capabilities()] == [ActionType.RESTART_SERVICE]
