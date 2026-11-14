from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

import httpx

from nightjar.connectors.base import (
    Capability,
    CapabilityReport,
    CheckFn,
    ConnectorError,
    ToolResult,
)
from nightjar.core.actions import ACTION_RISK
from nightjar.core.enums import ActionType, EvidenceKind
from nightjar.core.models import ActionSpec, VerificationPredicate

_LAB_TARGETS: dict[ActionType, tuple[str, ...]] = {
    ActionType.PURGE_CACHE: ("zone",),
    ActionType.ENABLE_UNDER_ATTACK: ("zone",),
    ActionType.REVOKE_CREDENTIAL: ("credential",),
    ActionType.QUARANTINE_MESSAGE: ("mailbox", "message"),
    ActionType.BLOCK_SENDER: ("mailbox", "sender"),
    ActionType.ROLLBACK_DEPLOYMENT: ("service", "deployment"),
    ActionType.RESTART_SERVICE: ("service",),
    ActionType.PROBE: ("url",),
}


class LabConnector:
    """Talks to the CornerShop synthetic environment.

    This connector exists so the whole product can be exercised without any
    third-party account. It is registered only when ``NIGHTJAR_LAB_ENABLED`` is
    true and is never the default for production action types that have a real
    connector configured.
    """

    name = "lab"
    action_types = frozenset(
        {
            ActionType.PURGE_CACHE,
            ActionType.ENABLE_UNDER_ATTACK,
            ActionType.REVOKE_CREDENTIAL,
            ActionType.QUARANTINE_MESSAGE,
            ActionType.BLOCK_SENDER,
            ActionType.ROLLBACK_DEPLOYMENT,
            ActionType.RESTART_SERVICE,
            ActionType.PROBE,
        }
    )
    tool_names = frozenset(
        {
            "lab.site_state",
            "lab.deployments",
            "lab.credentials",
            "lab.credential",
            "lab.mail_message",
            "lab.mail_reputation",
        }
    )

    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        *,
        exclude_actions: Iterable[ActionType] = (),
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        # A real connector for the same action always wins. Otherwise a plan
        # could name ``connector: "lab"`` and get a synthetic "revoked" result
        # while the exposed credential is still live at the provider.
        self.action_types = self.action_types - frozenset(exclude_actions)

    async def _request(
        self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method, f"{self._base}{path}", json=json, params=params
                )
            return response
        except httpx.HTTPError as exc:
            raise ConnectorError(f"lab connector unavailable: {exc}", retryable=True) from exc

    async def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._request(method, path, **kwargs)
        if response.status_code >= 400:
            raise ConnectorError(
                f"lab {method} {path} returned {response.status_code}: {response.text[:300]}"
            )
        body: dict[str, Any] = response.json()
        return body

    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        match action.action_type:
            case ActionType.PURGE_CACHE:
                return await self._json(
                    "POST",
                    "/lab/cache/purge",
                    json={"zone": action.params.get("zone"), "urls": action.params.get("urls")},
                )
            case ActionType.ENABLE_UNDER_ATTACK:
                return await self._json("POST", "/lab/under-attack", json={"enabled": True})
            case ActionType.REVOKE_CREDENTIAL:
                return await self._json(
                    "POST",
                    f"/lab/credentials/{action.params.get('credential_id')}/revoke",
                )
            case ActionType.QUARANTINE_MESSAGE:
                return await self._json(
                    "POST",
                    f"/lab/mail/{action.params.get('message_id')}/quarantine",
                )
            case ActionType.BLOCK_SENDER:
                return await self._json(
                    "POST", "/lab/mail/block", json={"sender": action.params.get("sender")}
                )
            case ActionType.ROLLBACK_DEPLOYMENT:
                return await self._json(
                    "POST",
                    "/lab/deploy/rollback",
                    json={
                        "service": action.params.get("service"),
                        "target_version": action.params.get("target_version"),
                    },
                )
            case ActionType.RESTART_SERVICE:
                return await self._json(
                    "POST", "/lab/service/restart", json={"service": action.params.get("service")}
                )
            case ActionType.PROBE:
                return await self._json("GET", "/lab/state")
            case _:
                raise ConnectorError(f"lab connector does not implement {action.action_type}")

    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult:
        if tool == "lab.site_state":
            state = await self._json("GET", "/lab/state")
            response = await self._request("GET", "/site/")
            checkout = await self._request("GET", "/site/checkout")
            return ToolResult(
                kind=EvidenceKind.HTTP_RESPONSE,
                source="lab.site_state",
                summary=(
                    f"site mode={state.get('site_mode')} homepage={response.status_code} "
                    f"checkout={checkout.status_code}"
                ),
                content={
                    "mode": state.get("site_mode"),
                    "deploy_version": state.get("deploy_version"),
                    "previous_good": state.get("previous_good"),
                    "under_attack": state.get("under_attack"),
                    "homepage_status": response.status_code,
                    "checkout_status": checkout.status_code,
                    "cache_header": response.headers.get("x-cache"),
                },
            )
        if tool == "lab.deployments":
            return ToolResult(
                kind=EvidenceKind.DEPLOYMENT,
                source="lab.deployments",
                summary="Recent deployment history",
                content=await self._json("GET", "/lab/deployments"),
            )
        if tool == "lab.credentials":
            return ToolResult(
                kind=EvidenceKind.SECRET_SCAN,
                source="lab.credentials",
                summary="Credential inventory",
                content=await self._json("GET", "/lab/credentials"),
            )
        if tool == "lab.credential":
            credential_id = str(params.get("credential_id", ""))
            if not credential_id:
                raise ConnectorError("lab.credential requires credential_id")
            return ToolResult(
                kind=EvidenceKind.SECRET_SCAN,
                source=f"lab.credential:{credential_id}",
                summary=f"Credential {credential_id} metadata",
                content=await self._json("GET", f"/lab/credentials/{credential_id}"),
            )
        if tool == "lab.mail_message":
            message_id = str(params.get("message_id", ""))
            if not message_id:
                raise ConnectorError("lab.mail_message requires message_id")
            return ToolResult(
                kind=EvidenceKind.EMAIL,
                source=f"lab.mail:{message_id}",
                summary="Reported phishing message",
                content=await self._json("GET", f"/lab/mail/{message_id}"),
            )
        if tool == "lab.mail_reputation":
            message_id = str(params.get("message_id", ""))
            if not message_id:
                raise ConnectorError("lab.mail_reputation requires message_id")
            return ToolResult(
                kind=EvidenceKind.DNS,
                source=f"lab.mail_reputation:{message_id}",
                summary="Sender authentication and domain reputation",
                content=await self._json("GET", f"/lab/mail/{message_id}/reputation"),
            )
        raise ConnectorError(f"unknown lab tool {tool}")

    def capabilities(self) -> tuple[Capability, ...]:
        return tuple(
            Capability(
                action_type=action,
                provider=self.name,
                target_kinds=_LAB_TARGETS[action],
                notes="synthetic CornerShop; not a production provider",
            )
            for action in sorted(self.action_types, key=lambda item: item.value)
        )

    async def preflight(self, *, deep: bool = False) -> CapabilityReport:
        started = time.monotonic()
        try:
            response = await self._request("GET", "/lab/state")
        except ConnectorError as exc:
            return CapabilityReport(provider=self.name, verdict="error", detail=str(exc))
        latency = int((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=f"lab returned {response.status_code}",
                latency_ms=latency,
            )
        return CapabilityReport(
            provider=self.name,
            verdict="ok",
            identity="cornershop synthetic environment",
            detail="all v1 scenarios are exercisable",
            latency_ms=latency,
        )

    def verification_checks(self) -> dict[str, CheckFn]:
        async def credential_revoked(predicate: VerificationPredicate) -> dict[str, Any]:
            credential_id = str(predicate.params.get("credential_id", ""))
            response = await self._request("GET", f"/lab/credentials/{credential_id}/verify")
            return {
                "credential_id": credential_id,
                "status": response.status_code,
                "passed": response.status_code == 401,
            }

        async def credential_works(predicate: VerificationPredicate) -> dict[str, Any]:
            credential_id = str(predicate.params.get("credential_id", ""))
            response = await self._request("GET", f"/lab/credentials/{credential_id}/verify")
            return {
                "credential_id": credential_id,
                "status": response.status_code,
                "passed": response.status_code == 200,
            }

        async def message_quarantined(predicate: VerificationPredicate) -> dict[str, Any]:
            message_id = str(predicate.params.get("message_id", ""))
            body = await self._json("GET", f"/lab/mail/{message_id}/quarantine-state")
            return {"message_id": message_id, **body, "passed": bool(body.get("quarantined"))}

        async def sender_blocked(predicate: VerificationPredicate) -> dict[str, Any]:
            sender = str(predicate.params.get("sender", ""))
            response = await self._request("POST", "/lab/mail/send-test", json={"sender": sender})
            return {
                "sender": sender,
                "smtp_status": response.status_code,
                "passed": response.status_code == 550,
            }

        async def checkout_ok(predicate: VerificationPredicate) -> dict[str, Any]:
            url = str(predicate.params.get("url", f"{self._base}/site/checkout"))
            response = await self._request("GET", url.removeprefix(self._base))
            return {
                "url": url,
                "status": response.status_code,
                "body": response.json()
                if response.headers.get("content-type", "").startswith("application/json")
                else {},
                "passed": response.status_code == 200,
            }

        return {
            "credential_revoked": credential_revoked,
            "credential_works": credential_works,
            "message_quarantined": message_quarantined,
            "sender_blocked": sender_blocked,
            "checkout_ok": checkout_ok,
        }


__all__ = ["ACTION_RISK", "LabConnector"]
