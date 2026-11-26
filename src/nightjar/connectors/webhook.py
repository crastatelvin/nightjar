from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from nightjar.connectors.base import Capability, CapabilityReport, ConnectorError
from nightjar.core.enums import ActionType
from nightjar.core.models import ActionSpec


class WebhookConnector:
    """Delivers notifications to the single configured webhook destination.

    The destination is configuration, never model output, so a prompt-injected
    plan cannot turn Nightjar into an outbound request primitive.

    When ``NIGHTJAR_NOTIFY_WEBHOOK_SECRET`` is set, each delivery is signed
    Stripe-style so the receiver can reject forged posts::

        x-nightjar-signature: t=1731500000,v1=<hex hmac-sha256 of "t.body">
    """

    name = "webhook"
    action_types = frozenset({ActionType.NOTIFY})
    tool_names: frozenset[str] = frozenset()

    def __init__(self, url: str, *, secret: str | None = None, timeout: float = 10.0) -> None:
        self._url = url
        self._secret = secret
        self._timeout = timeout

    def _headers(self, body: bytes) -> dict[str, str]:
        headers = {"content-type": "application/json", "user-agent": "nightjar/0.1"}
        if not self._secret:
            return headers
        timestamp = str(int(time.time()))
        signed = timestamp.encode() + b"." + body
        digest = hmac.new(self._secret.encode(), signed, hashlib.sha256).hexdigest()
        headers["x-nightjar-signature"] = f"t={timestamp},v1={digest}"
        return headers

    async def _post(self, payload: dict[str, Any]) -> int:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, content=body, headers=self._headers(body))
        except httpx.HTTPError as exc:
            raise ConnectorError(f"notification delivery failed: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            raise ConnectorError(
                f"notification endpoint returned {response.status_code}", retryable=True
            )
        return response.status_code

    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        if action.action_type is not ActionType.NOTIFY:
            raise ConnectorError(f"webhook connector does not implement {action.action_type}")
        status = await self._post(
            {
                "event": "nightjar.notify",
                "channel": action.params.get("channel", "nightjar"),
                "message": action.params.get("message", ""),
                "incident_id": action.params.get("incident_id"),
            }
        )
        return {"delivered": True, "status": status, "signed": self._secret is not None}

    def capabilities(self) -> tuple[Capability, ...]:
        return (
            Capability(
                action_type=ActionType.NOTIFY,
                provider=self.name,
                target_kinds=("channel",),
                notes="outbound only; destination is configuration",
                proof="receiver acknowledgement; `doctor --deep` sends a signed ping",
            ),
        )

    async def preflight(self, *, deep: bool = False) -> CapabilityReport:
        parts = urlsplit(self._url)
        identity = f"{parts.scheme}://{parts.netloc}{parts.path}"
        if parts.scheme != "https" and parts.hostname not in {"127.0.0.1", "localhost"}:
            return CapabilityReport(
                provider=self.name,
                verdict="degraded",
                identity=identity,
                detail="destination is not HTTPS",
            )
        if not deep:
            return CapabilityReport(
                provider=self.name,
                verdict="ok",
                identity=identity,
                detail="configured; run with --deep to send a signed ping",
            )
        started = time.monotonic()
        try:
            status = await self._post(
                {"event": "nightjar.doctor", "dry_run": True, "channel": "nightjar"}
            )
        except ConnectorError as exc:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                identity=identity,
                detail=str(exc),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        return CapabilityReport(
            provider=self.name,
            verdict="ok",
            identity=identity,
            detail=(
                f"signed ping {status}"
                if self._secret
                else f"unsigned ping {status} (set NIGHTJAR_NOTIFY_WEBHOOK_SECRET)"
            ),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def collect(self, tool: str, params: dict[str, Any]) -> Any:
        raise ConnectorError("webhook connector has no read tools")

    def verification_checks(self) -> dict[str, Any]:
        return {}
