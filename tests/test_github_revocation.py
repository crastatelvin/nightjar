from __future__ import annotations

import os

import pytest
import respx
from httpx import Response

from nightjar.connectors.base import ConnectorError
from nightjar.connectors.github import GitHubConnector
from nightjar.core.enums import ActionType, PredicateKind, RiskClass
from nightjar.core.models import ActionSpec, VerificationPredicate
from nightjar.core.secrets import EnvVault, SecretError, fingerprint

TOKEN = "ghp_" + "a" * 36


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> EnvVault:
    monkeypatch.setenv("EXPOSED_PAT", TOKEN)
    return EnvVault()


def revoke_action(**params: object) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.REVOKE_CREDENTIAL,
        target="github",
        risk=RiskClass.R2,
        params=dict(params),
    )


def revoked_predicate() -> VerificationPredicate:
    return VerificationPredicate(
        kind=PredicateKind.CONNECTOR_CHECK,
        description="the exposed PAT is rejected by GitHub",
        params={
            "check": "github.credential_revoked",
            "credential_ref": "env:EXPOSED_PAT",
        },
    )


@respx.mock
async def test_revocation_is_unauthenticated_and_never_returns_the_token(
    vault: EnvVault,
) -> None:
    route = respx.post("https://api.github.com/credentials/revoke").mock(
        return_value=Response(202, json={})
    )
    connector = GitHubConnector(None, vault=vault)

    result = await connector.execute(revoke_action(credential_ref="env:EXPOSED_PAT"))

    assert route.called
    request = route.calls[0].request
    assert request.headers.get("authorization") is None
    assert request.headers["x-github-api-version"] == "2026-03-10"
    assert TOKEN.encode() in request.content
    assert result["requested"] is True
    assert result["status"] == 202
    assert result["fingerprint"] == fingerprint(TOKEN)
    assert result["hint"] == TOKEN[-4:]
    assert TOKEN not in str(result)


@respx.mock
async def test_an_authenticated_revocation_is_reported_as_a_configuration_error(
    vault: EnvVault,
) -> None:
    respx.post("https://api.github.com/credentials/revoke").mock(
        return_value=Response(403, json={"message": "authenticated requests are not allowed"})
    )
    connector = GitHubConnector("some-token", vault=vault)

    with pytest.raises(ConnectorError, match="must be called without a token"):
        await connector.execute(revoke_action(credential_ref="env:EXPOSED_PAT"))


@respx.mock
async def test_rate_limit_is_retryable(vault: EnvVault) -> None:
    respx.post("https://api.github.com/credentials/revoke").mock(
        return_value=Response(429, json={"message": "rate limited"})
    )
    connector = GitHubConnector(None, vault=vault)

    with pytest.raises(ConnectorError) as excinfo:
        await connector.execute(revoke_action(credential_ref="env:EXPOSED_PAT"))
    assert excinfo.value.retryable is True
    assert "60" in str(excinfo.value)


async def test_a_non_github_credential_is_refused_before_any_request(
    vault: EnvVault,
) -> None:
    connector = GitHubConnector(None, vault=vault)
    action = revoke_action(credential_ref="env:EXPOSED_PAT")
    action.params["credential_ref"] = "env:STRIPE_KEY"
    os.environ["STRIPE_KEY"] = "sk_live_" + "x" * 24

    with pytest.raises(ConnectorError, match="not a GitHub token"):
        await connector.execute(action)


async def test_a_missing_reference_is_a_clear_error(vault: EnvVault) -> None:
    connector = GitHubConnector(None, vault=vault)
    with pytest.raises(ConnectorError, match="credential_ref"):
        await connector.execute(revoke_action())


async def test_an_unset_environment_secret_names_the_variable_not_a_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISSING_PAT", raising=False)
    with pytest.raises(SecretError, match="MISSING_PAT"):
        EnvVault().resolve("env:MISSING_PAT")


@respx.mock
async def test_a_revoked_token_passes_the_verifier(vault: EnvVault) -> None:
    respx.get("https://api.github.com/user").mock(return_value=Response(401, json={}))
    connector = GitHubConnector(None, vault=vault)

    observed = await connector.verification_checks()["github.credential_revoked"](
        revoked_predicate()
    )

    assert observed["passed"] is True
    assert observed["status"] == 401
    assert observed["fingerprint"] == fingerprint(TOKEN)
    assert TOKEN not in str(observed)


@respx.mock
async def test_a_live_token_fails_the_verifier(vault: EnvVault) -> None:
    respx.get("https://api.github.com/user").mock(
        return_value=Response(200, json={"login": "someone"})
    )
    connector = GitHubConnector(None, vault=vault)

    observed = await connector.verification_checks()["github.credential_revoked"](
        revoked_predicate()
    )

    assert observed["passed"] is False
    assert observed["status"] == 200


@respx.mock
async def test_a_forbidden_probe_is_not_mistaken_for_revocation(vault: EnvVault) -> None:
    """403 means the token was accepted and then refused, not that it is dead."""
    respx.get("https://api.github.com/user").mock(return_value=Response(403, json={}))
    connector = GitHubConnector(None, vault=vault)

    observed = await connector.verification_checks()["github.credential_revoked"](
        revoked_predicate()
    )

    assert observed["passed"] is False
    assert observed["status"] == 403
