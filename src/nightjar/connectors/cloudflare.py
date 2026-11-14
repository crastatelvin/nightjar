from __future__ import annotations

import time
from typing import Any

import httpx

from nightjar.connectors.base import Capability, CapabilityReport, ConnectorError
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

    def capabilities(self) -> tuple[Capability, ...]:
        return (
            Capability(
                action_type=ActionType.PURGE_CACHE,
                provider=self.name,
                target_kinds=("zone", "url"),
                notes="purge by URL or everything",
            ),
            Capability(
                action_type=ActionType.ENABLE_UNDER_ATTACK,
                provider=self.name,
                target_kinds=("zone",),
                notes="security level under_attack; customer-facing, needs approval",
            ),
        )

    async def preflight(self, *, deep: bool = False) -> CapabilityReport:
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self._base}/user/tokens/verify",
                    headers={"authorization": f"Bearer {self._token}"},
                )
        except httpx.HTTPError as exc:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=f"cloudflare unreachable: {exc}",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        latency = int((time.monotonic() - started) * 1000)
        if response.status_code in {401, 403}:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=f"token rejected ({response.status_code})",
                latency_ms=latency,
            )
        body = response.json() if response.content else {}
        if not body.get("success", False):
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=f"token verification failed: {body.get('errors')}",
                latency_ms=latency,
            )
        status = str((body.get("result") or {}).get("status", ""))
        if status and status != "active":
            return CapabilityReport(
                provider=self.name,
                verdict="degraded",
                detail=f"token status is {status}",
                latency_ms=latency,
            )
        token_id = str((body.get("result") or {}).get("id", ""))
        return CapabilityReport(
            provider=self.name,
            verdict="ok",
            identity=f"token:{token_id[:8]}…" if token_id else "token verified",
            detail="zone permissions are checked on the first action",
            latency_ms=latency,
        )

    async def collect(self, tool: str, params: dict[str, Any]) -> Any:
        raise ConnectorError("cloudflare connector has no read tools in v1")

    def verification_checks(self) -> dict[str, Any]:
        return {}
