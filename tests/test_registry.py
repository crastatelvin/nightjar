from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nightjar.api.settings import Settings
from nightjar.connectors.base import ConnectorError, ToolResult
from nightjar.connectors.http import HttpConnector
from nightjar.connectors.lab import LabConnector
from nightjar.connectors.registry import ConnectorRegistry, build_registry
from nightjar.core.actions import ActionValidationError
from nightjar.core.enums import ActionType, RiskClass
from nightjar.core.models import ActionSpec


def names(settings: Settings) -> list[str]:
    return sorted(connector.name for connector in build_registry(settings).connectors)


def purge(connector: str | None = None) -> ActionSpec:
    params: dict[str, Any] = {"zone": "cornershop", "urls": ["https://example.com"]}
    if connector:
        params["connector"] = connector
    return ActionSpec(
        action_type=ActionType.PURGE_CACHE,
        target="cornershop",
        params=params,
        risk=RiskClass.R1,
    )


class FakeCacheConnector:
    name = "fake-cdn"
    action_types = frozenset({ActionType.PURGE_CACHE})
    tool_names: frozenset[str] = frozenset()

    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        return {"connector": self.name, "action": action.action_type.value}

    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult:
        raise ConnectorError("not implemented")

    def verification_checks(self) -> dict[str, Any]:
        return {}


def test_unambiguous_action_routes_to_its_only_connector() -> None:
    connector = LabConnector("http://127.0.0.1:8100")
    registry = ConnectorRegistry([connector, HttpConnector()])
    assert registry.connector_for(purge()) is connector
    assert "lab.site_state" in registry.tools()
    assert "http.get" in registry.tools()


def test_ambiguous_action_requires_an_explicit_connector() -> None:
    lab = LabConnector("http://127.0.0.1:8100")
    fake = FakeCacheConnector()
    registry = ConnectorRegistry([lab, fake])
    with pytest.raises(ConnectorError, match="ambiguous connector"):
        registry.connector_for(purge())
    assert registry.connector_for(purge("lab")) is lab
    assert registry.connector_for(purge("fake-cdn")) is fake


def test_named_connector_must_implement_the_action() -> None:
    lab = LabConnector("http://127.0.0.1:8100")
    registry = ConnectorRegistry([lab])
    with pytest.raises(ConnectorError, match="no connector available"):
        registry.connector_for(purge("cloudflare"))


def test_duplicate_tool_registration_is_rejected() -> None:
    class DuplicateTools(LabConnector):
        pass

    with pytest.raises(ValueError, match="duplicate tool registration"):
        ConnectorRegistry([LabConnector("http://127.0.0.1:8100"), DuplicateTools("http://x")])


def test_unknown_read_tool_is_rejected() -> None:
    registry = ConnectorRegistry([LabConnector("http://127.0.0.1:8100")])
    with pytest.raises(ConnectorError, match="not in the read-only registry"):
        asyncio.run(registry.collect("shell.exec", {"command": "id"}))


def test_oversized_probe_parameters_are_rejected() -> None:
    registry = ConnectorRegistry([HttpConnector()])
    with pytest.raises(ConnectorError, match="too long"):
        asyncio.run(registry.collect("http.get", {"url": "https://example.com/" + "a" * 3000}))


def test_http_connector_is_read_only() -> None:
    connector = HttpConnector()
    with pytest.raises(ConnectorError, match="read-only"):
        asyncio.run(
            connector.execute(
                ActionSpec(
                    action_type=ActionType.PURGE_CACHE,
                    target="x",
                    params={"zone": "z", "urls": ["https://example.com"]},
                    risk=RiskClass.R1,
                )
            )
        )


def test_registry_refuses_invalid_actions_before_dispatch() -> None:
    registry = ConnectorRegistry([LabConnector("http://127.0.0.1:8100")])
    invalid = purge().model_copy(update={"risk": RiskClass.R0})
    with pytest.raises(ActionValidationError):
        asyncio.run(registry.execute(invalid))


# --------------------------------------------------------------- build_registry
def test_development_registry_has_the_lab_and_the_read_only_prober() -> None:
    assert names(Settings(_env_file=None)) == ["http", "lab"]


def test_provider_connectors_are_absent_without_credentials() -> None:
    assert "github" not in names(Settings(_env_file=None))
    assert "cloudflare" not in names(Settings(_env_file=None))
    assert "webhook" not in names(Settings(_env_file=None))


def test_a_github_token_enables_issues_and_revocation_together() -> None:
    settings = Settings(_env_file=None, github_token="ghp_" + "a" * 36)
    assert "github" in names(settings)
    registry = build_registry(settings)
    assert "github.repo_file" in registry.tools()
    assert "github.credential_revoked" in registry.verification_checks()


def test_revocation_can_be_enabled_without_a_github_token() -> None:
    """The revocation endpoint is unauthenticated; issue creation still is not."""
    settings = Settings(_env_file=None, github_credential_revocation=True)
    registry = build_registry(settings)
    assert "github" in names(settings)
    connector = registry.connector_for(
        ActionSpec(
            action_type=ActionType.REVOKE_CREDENTIAL,
            target="github",
            params={"credential_ref": "env:EXPOSED_PAT"},
            risk=RiskClass.R2,
        )
    )
    assert connector.name == "github"


def test_the_lab_never_shadows_a_real_connector_for_the_same_action() -> None:
    settings = Settings(_env_file=None, github_credential_revocation=True)
    registry = build_registry(settings)
    lab = next(connector for connector in registry.connectors if connector.name == "lab")
    assert ActionType.REVOKE_CREDENTIAL not in lab.action_types
    # The actions with no real implementation still fall back to the lab.
    assert ActionType.QUARANTINE_MESSAGE in lab.action_types
    # And asking for the lab by name cannot fake a revocation.
    with pytest.raises(ConnectorError, match="no connector available"):
        registry.connector_for(
            ActionSpec(
                action_type=ActionType.REVOKE_CREDENTIAL,
                target="github",
                params={"connector": "lab", "credential_ref": "env:EXPOSED_PAT"},
                risk=RiskClass.R2,
            )
        )


def test_cloudflare_and_webhook_appear_only_when_configured() -> None:
    settings = Settings(
        _env_file=None,
        cloudflare_token="cf-token",
        notify_webhook_url="https://hooks.example.com/nightjar",
    )
    assert names(settings) == ["cloudflare", "http", "lab", "webhook"]


def test_every_registered_tool_and_check_name_is_unique() -> None:
    settings = Settings(
        _env_file=None,
        github_token="ghp_" + "a" * 36,
        cloudflare_token="cf-token",
        notify_webhook_url="https://hooks.example.com/nightjar",
    )
    registry = build_registry(settings)
    tools = [tool for connector in registry.connectors for tool in connector.tool_names]
    assert len(tools) == len(set(tools))


def test_providers_for_lists_every_capable_connector() -> None:
    registry = ConnectorRegistry([LabConnector("http://127.0.0.1:8100"), FakeCacheConnector()])
    assert registry.providers_for(ActionType.PURGE_CACHE) == ["fake-cdn", "lab"]
    assert registry.providers_for(ActionType.NOTIFY) == []


def test_an_unambiguous_provider_is_bound_to_the_action() -> None:
    from typing import cast

    from nightjar.agents.mock import MockAgentRuntime
    from nightjar.api.service import IncidentService

    service = IncidentService(
        settings=Settings(_env_file=None),
        repository=cast("Any", None),
        runtime=MockAgentRuntime(),
        registry=ConnectorRegistry([LabConnector("http://127.0.0.1:8100")]),
    )
    action = purge()
    action.params.pop("connector", None)

    bound, ambiguous = service._bind_providers([action])

    assert ambiguous == []
    assert bound[0].params["connector"] == "lab"
    # The original action is not mutated.
    assert "connector" not in action.params


def test_an_ambiguous_provider_is_reported_instead_of_guessed() -> None:
    from typing import cast

    from nightjar.agents.mock import MockAgentRuntime
    from nightjar.api.service import IncidentService

    service = IncidentService(
        settings=Settings(_env_file=None),
        repository=cast("Any", None),
        runtime=MockAgentRuntime(),
        registry=ConnectorRegistry([LabConnector("http://127.0.0.1:8100"), FakeCacheConnector()]),
    )
    action = purge()
    action.params.pop("connector", None)

    bound, ambiguous = service._bind_providers([action])

    assert bound[0].params.get("connector") is None
    assert ambiguous == ["purge_cache (fake-cdn, lab)"]
