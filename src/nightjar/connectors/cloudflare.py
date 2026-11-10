from __future__ import annotations

from typing import Any

import httpx

from nightjar.connectors.base import ConnectorError
from nightjar.core.enums import ActionType
from nightjar.core.models import ActionSpec


class CloudflareConnector:
    name = "cloudflare"
    action_types = frozenset({ActionType.PURGE_CACHE, ActionType.ENABLE_UNDER_ATTACK})
    tool_names: frozenset[str] = frozenset()

    def __init__(
        self, token: str, *, base_url: str = "https://api.cloudflare.com/client/v4"
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")

    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        zone = str(action.params.get("zone_id") or action.params.get("zone") or action.target)
        if not zone:
            raise ConnectorError("cloudflare action requires a zone id")
        headers = {"authorization": f"Bearer {self._token}"}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                if action.action_type is ActionType.PURGE_CACHE:
                    response = await client.post(
                        f"{self._base}/zones/{zone}/purge_cache",
                        headers=headers,
                        json={"files": action.params.get("urls", [])},
                    )
                elif action.action_type is ActionType.ENABLE_UNDER_ATTACK:
                    response = await client.patch(
                        f"{self._base}/zones/{zone}/settings/security_level",
                        headers=headers,
                        json={"value": "under_attack"},
                    )
                else:
                    raise ConnectorError(
                        f"cloudflare connector does not implement {action.action_type}"
                    )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"cloudflare unavailable: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            raise ConnectorError(
                f"cloudflare action failed: {response.status_code} {response.text[:300]}"
            )
        body: dict[str, Any] = response.json()
        if not body.get("success", False):
            raise ConnectorError(f"cloudflare rejected the action: {body.get('errors')}")
        return {"success": True, "result": body.get("result")}

    async def collect(self, tool: str, params: dict[str, Any]) -> Any:
        raise ConnectorError("cloudflare connector has no read tools in v1")

    def verification_checks(self) -> dict[str, Any]:
        return {}
