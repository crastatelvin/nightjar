from __future__ import annotations

import time
from typing import Any

import httpx

from nightjar.connectors.base import (
    Capability,
    CapabilityReport,
    CheckFn,
    ConnectorError,
    ManualStep,
    ToolResult,
)
from nightjar.core.enums import ActionType
from nightjar.core.models import ActionSpec
from nightjar.core.secrets import EnvVault, SecretError, fingerprint, hint

STRIPE_CREDENTIAL_PREFIXES = ("sk_", "rk_")


class StripeConnector:
    """Assisted revocation for Stripe secret and restricted keys.

    Stripe has no API to expire a key: lifecycle is Dashboard-only. Nightjar
    therefore surfaces the exact step, identifies the key by fingerprint and
    last four characters, and proves the key is dead by presenting it and
    requiring a 401. It never reports the action as executed.
    """

    name = "stripe"
    action_types = frozenset({ActionType.REVOKE_CREDENTIAL})
    tool_names: frozenset[str] = frozenset()

    def __init__(
        self,
        secret_key: str | None = None,
        *,
        base_url: str = "https://api.stripe.com",
        timeout: float = 15.0,
    ) -> None:
        self._key = secret_key or None
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        raise ConnectorError(
            "stripe key revocation is Dashboard-only; this action is a manual step and "
            "must not be executed automatically"
        )

    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult:
        raise ConnectorError(f"stripe exposes no read tools, got {tool!r}")

    def verification_checks(self) -> dict[str, CheckFn]:
        return {}

    def manual_step(self, action: ActionSpec) -> ManualStep:
        ref = str(action.params.get("credential_ref", ""))
        if not ref:
            raise ConnectorError(
                "stripe manual revocation requires credential_ref (env:NAME or file:/path) "
                "so the key can be identified and probed"
            )
        try:
            raw = EnvVault().resolve(ref)
        except SecretError as exc:
            raise ConnectorError(f"stripe manual step cannot read credential_ref: {exc}") from exc
        if not raw.startswith(STRIPE_CREDENTIAL_PREFIXES):
            raise ConnectorError(
                "credential is not a Stripe key (expected sk_ or rk_); "
                "refusing to describe the wrong provider's step"
            )
        fp = fingerprint(raw)
        key_name = str(
            action.params.get("key_name") or action.params.get("credential_id") or "the key"
        )
        return ManualStep(
            provider=self.name,
            title="Expire the exposed Stripe key",
            instructions=(
                "Open the Stripe Dashboard API keys page.",
                f"Find {key_name} ending {hint(raw)} (fingerprint {fp}).",
                "Choose the ⋯ menu next to the key, then Expire key, and confirm.",
                "Return to Nightjar and attest the step. The verifier will present the "
                "key and require a 401 before the incident can resolve.",
            ),
            dashboard_url="https://dashboard.stripe.com/apikeys",
            fingerprint=fp,
            probe_url=f"{self._base}/v1/balance",
            auth="basic",
        )

    def capabilities(self) -> tuple[Capability, ...]:
        return (
            Capability(
                action_type=ActionType.REVOKE_CREDENTIAL,
                provider=self.name,
                automated=False,
                target_kinds=("credential",),
                notes=(
                    "Dashboard-only; Nightjar surfaces the step and proves the key is dead "
                    "with a 401 probe"
                ),
            ),
        )

    async def preflight(self, *, deep: bool = False) -> CapabilityReport:
        if not self._key:
            return CapabilityReport(
                provider=self.name,
                verdict="ok",
                identity="manual step only",
                detail="set NIGHTJAR_STRIPE_SECRET_KEY to probe a read-only key",
            )
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self._base}/v1/balance", auth=httpx.BasicAuth(self._key, "")
                )
        except httpx.HTTPError as exc:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=f"stripe unavailable: {exc}",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        latency = int((time.monotonic() - started) * 1000)
        if response.status_code == 401:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail="NIGHTJAR_STRIPE_SECRET_KEY was rejected (401)",
                latency_ms=latency,
            )
        if response.status_code == 403:
            # Stripe accepted the key and refused the scope. A least-privilege
            # restricted key is the recommended setup, and the manual step does
            # not need balance read: the probe presents the exposed key.
            return CapabilityReport(
                provider=self.name,
                verdict="ok",
                identity="restricted key verified",
                detail="key is live; balance read is not required for the manual step",
                latency_ms=latency,
            )
        if response.status_code >= 400:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=f"stripe returned {response.status_code}",
                latency_ms=latency,
            )
        return CapabilityReport(
            provider=self.name,
            verdict="ok",
            identity="restricted key verified",
            detail="read-only probe key is live",
            latency_ms=latency,
        )


__all__ = ["STRIPE_CREDENTIAL_PREFIXES", "StripeConnector"]
