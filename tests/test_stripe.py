from __future__ import annotations

import base64

import httpx
import pytest
import respx
from httpx import Response

from nightjar.connectors.base import ConnectorError
from nightjar.connectors.stripe import StripeConnector
from nightjar.core.enums import ActionType, RiskClass
from nightjar.core.models import ActionSpec
from nightjar.core.secrets import fingerprint

BASE = "https://api.stripe.com"
KEY = "sk_live_51NExampleSecretKey"
REF = "env:NIGHTJAR_TEST_STRIPE_KEY"


@pytest.fixture
def exposed_key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("NIGHTJAR_TEST_STRIPE_KEY", KEY)
    return KEY


def revoke(**params: object) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.REVOKE_CREDENTIAL,
        target="cred-1",
        params={"connector": "stripe", "credential_id": "cred-1", **params},
        risk=RiskClass.R2,
    )


def test_manual_step_names_the_key_without_revealing_it(exposed_key: str) -> None:
    connector = StripeConnector("rk_live_readonly")
    step = connector.manual_step(revoke(credential_ref=REF, key_name="Checkout key"))

    assert step.provider == "stripe"
    assert step.dashboard_url == "https://dashboard.stripe.com/apikeys"
    assert step.fingerprint == fingerprint(KEY)
    assert step.probe_url == f"{BASE}/v1/balance"
    assert step.auth == "basic"
    joined = " ".join(step.instructions)
    assert "Checkout key" in joined
    assert KEY[-4:] in joined
    assert KEY not in joined


def test_manual_step_requires_a_vault_reference() -> None:
    with pytest.raises(ConnectorError, match="credential_ref"):
        StripeConnector(None).manual_step(revoke())


def test_manual_step_refuses_another_providers_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NIGHTJAR_TEST_GITHUB_KEY", "ghp_exampleToken")
    with pytest.raises(ConnectorError, match="not a Stripe key"):
        StripeConnector(None).manual_step(revoke(credential_ref="env:NIGHTJAR_TEST_GITHUB_KEY"))


async def test_stripe_never_claims_to_execute_the_revocation(exposed_key: str) -> None:
    with pytest.raises(ConnectorError, match="Dashboard-only"):
        await StripeConnector(None).execute(revoke(credential_ref=REF))


def test_the_capability_is_manual_and_satisfies_the_requirement() -> None:
    capabilities = StripeConnector(None).capabilities()
    assert len(capabilities) == 1
    assert capabilities[0].action_type is ActionType.REVOKE_CREDENTIAL
    assert capabilities[0].automated is False


async def test_preflight_without_a_key_reports_manual_mode() -> None:
    report = await StripeConnector(None).preflight()
    assert report.verdict == "ok"
    assert report.identity == "manual step only"


@respx.mock
async def test_preflight_proves_the_configured_key_with_basic_auth() -> None:
    route = respx.get(f"{BASE}/v1/balance").mock(return_value=Response(200, json={}))
    report = await StripeConnector("rk_live_readonly").preflight()

    assert report.verdict == "ok"
    header = route.calls[0].request.headers["authorization"]
    assert header.startswith("Basic ")
    assert base64.b64decode(header[6:]).decode() == "rk_live_readonly:"


@respx.mock
async def test_preflight_reports_a_rejected_key() -> None:
    respx.get(f"{BASE}/v1/balance").mock(return_value=Response(401, json={}))
    report = await StripeConnector("sk_live_dead").preflight()
    assert report.verdict == "error"
    assert "rejected" in report.detail


@respx.mock
async def test_a_403_proves_a_least_privilege_key_is_live() -> None:
    respx.get(f"{BASE}/v1/balance").mock(return_value=Response(403, json={}))
    report = await StripeConnector("rk_live_readonly").preflight()
    # The manual step does not need balance read; a restricted key is the
    # recommended configuration, so this is healthy rather than degraded.
    assert report.verdict == "ok"
    assert "balance read is not required" in report.detail


@respx.mock
async def test_preflight_reports_an_unreachable_api() -> None:
    respx.get(f"{BASE}/v1/balance").mock(side_effect=httpx.ConnectError("dns"))
    report = await StripeConnector("rk_live_readonly").preflight()
    assert report.verdict == "error"
    assert "unavailable" in report.detail
