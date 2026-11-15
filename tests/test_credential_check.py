from __future__ import annotations

import base64

import pytest
import respx
from httpx import Response

from nightjar.core.enums import PredicateKind
from nightjar.core.models import VerificationPredicate
from nightjar.core.secrets import EnvVault
from nightjar.core.verification import CredentialCheck, VerificationError

URL = "https://api.stripe.com/v1/balance"
REF = "env:NIGHTJAR_TEST_EXPOSED_KEY"
KEY = "sk_live_51NExampleSecretKey"


@pytest.fixture
def exposed(monkeypatch: pytest.MonkeyPatch) -> CredentialCheck:
    monkeypatch.setenv("NIGHTJAR_TEST_EXPOSED_KEY", KEY)
    return CredentialCheck(EnvVault())


def predicate(**params: object) -> VerificationPredicate:
    return VerificationPredicate(
        kind=PredicateKind.CREDENTIAL,
        description="the key is rejected",
        params={"credential_ref": REF, "probe_url": URL, **params},
    )


@respx.mock
async def test_bearer_is_the_default_and_401_passes(exposed: CredentialCheck) -> None:
    route = respx.get(URL).mock(return_value=Response(401, json={}))
    result = await exposed(predicate())

    assert result["passed"] is True
    assert result["auth"] == "bearer"
    assert route.calls[0].request.headers["authorization"] == f"Bearer {KEY}"


@respx.mock
async def test_basic_auth_sends_the_key_as_the_username(exposed: CredentialCheck) -> None:
    route = respx.get(URL).mock(return_value=Response(401, json={}))
    result = await exposed(predicate(auth="basic"))

    assert result["passed"] is True
    header = route.calls[0].request.headers["authorization"]
    assert header.startswith("Basic ")
    assert base64.b64decode(header[6:]).decode() == f"{KEY}:"


@respx.mock
async def test_a_live_key_fails_even_when_it_lacks_permission(
    exposed: CredentialCheck,
) -> None:
    respx.get(URL).mock(return_value=Response(403, json={}))
    result = await exposed(predicate(auth="basic"))

    # 403 means Stripe accepted the key and refused the scope. The key is live.
    assert result["passed"] is False
    assert result["status"] == 403


@respx.mock
async def test_a_200_fails(exposed: CredentialCheck) -> None:
    respx.get(URL).mock(return_value=Response(200, json={}))
    assert (await exposed(predicate()))["passed"] is False


async def test_an_unknown_auth_scheme_is_rejected(exposed: CredentialCheck) -> None:
    with pytest.raises(VerificationError, match=r"bearer.*basic"):
        await exposed(predicate(auth="digest"))


async def test_a_reference_and_url_are_required(exposed: CredentialCheck) -> None:
    with pytest.raises(VerificationError, match="credential_ref"):
        await exposed(predicate(credential_ref=""))
    with pytest.raises(VerificationError, match="probe_url"):
        await exposed(predicate(probe_url=""))
