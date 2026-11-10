from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nightjar.connectors.base import ConnectorError, ToolResult
from nightjar.connectors.http import HttpConnector
from nightjar.connectors.lab import LabConnector
from nightjar.connectors.registry import ConnectorRegistry
from nightjar.core.actions import ActionValidationError
from nightjar.core.enums import ActionType, RiskClass
from nightjar.core.models import ActionSpec


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
