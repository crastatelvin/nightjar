from __future__ import annotations

from typing import Any

import httpx

from nightjar.connectors.base import ConnectorError
from nightjar.core.enums import ActionType
from nightjar.core.models import ActionSpec


class WebhookConnector:
    """Delivers notifications to the single configured webhook destination.

    The destination is configuration, never model output, so a prompt-injected
    plan cannot turn Nightjar into an outbound request primitive.
    """

    name = "webhook"
    action_types = frozenset({ActionType.NOTIFY})
    tool_names: frozenset[str] = frozenset()

    def __init__(self, url: str, *, timeout: float = 10.0) -> None:
        self._url = url
        self._timeout = timeout

    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        if action.action_type is not ActionType.NOTIFY:
            raise ConnectorError(f"webhook connector does not implement {action.action_type}")
        payload = {
            "channel": action.params.get("channel", "nightjar"),
            "message": action.params.get("message", ""),
            "incident_id": action.params.get("incident_id"),
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, json=payload)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"notification delivery failed: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            raise ConnectorError(f"notification endpoint returned {response.status_code}")
        return {"delivered": True, "status": response.status_code}

    async def collect(self, tool: str, params: dict[str, Any]) -> Any:
        raise ConnectorError("webhook connector has no read tools")

    def verification_checks(self) -> dict[str, Any]:
        return {}
