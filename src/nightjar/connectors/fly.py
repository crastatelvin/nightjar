from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx

from nightjar.connectors.base import (
    Capability,
    CapabilityReport,
    CheckFn,
    ConnectorError,
    ToolResult,
)
from nightjar.connectors.errors import (
    NotFound,
    PermissionDenied,
    RateLimited,
    error_from_response,
    retry_after_seconds,
    transport_error,
)
from nightjar.connectors.ratelimit import limiter_for
from nightjar.core.enums import ActionType, EvidenceKind
from nightjar.core.models import ActionSpec, VerificationPredicate
from nightjar.core.verification import VerificationError


class FlyConnector:
    """Machine restart on Fly.io, verified by state and update time.

    A restart request returns immediately. The machine can still be
    `restarting`, or it can come back with the same `updated_at` because the
    request never reached the allocator. Both are failures until a read proves
    `started` with a newer timestamp.
    """

    name = "fly"
    action_types = frozenset({ActionType.RESTART_SERVICE})
    tool_names = frozenset({"fly.machines"})

    def __init__(
        self,
        api_token: str,
        *,
        base_url: str = "https://api.machines.dev",
        app: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._token = api_token
        self._base = base_url.rstrip("/")
        self._app = app or None
        self._timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._token}"}

    def _app_name(self, params: dict[str, Any]) -> str:
        # Precedence mirrors core/targets.py: the plan value wins over config.
        app = str(
            params.get("app")
            or params.get("app_id")
            or params.get("service")
            or params.get("service_id")
            or self._app
            or ""
        )
        if not app:
            raise ConnectorError("fly action requires an app: set params.app or NIGHTJAR_FLY_APP")
        return app

    def _machine(self, params: dict[str, Any]) -> str:
        machine = str(params.get("machine_id") or params.get("machine") or "")
        if not machine:
            raise ConnectorError(
                "fly restart requires machine_id; list them with the fly.machines tool"
            )
        return machine

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        await limiter_for(self.name).acquire()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.request(
                    method,
                    f"{self._base}{path}",
                    headers=self._headers,
                    params=params,
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise transport_error(self.name, exc, f"{method} {path}") from exc

    def _unwrap(self, response: httpx.Response, what: str) -> Any:
        if response.status_code == 401:
            raise PermissionDenied(
                f"fly refused {what} (401): the token is invalid",
                status=401,
                provider=self.name,
            )
        if response.status_code == 403:
            raise PermissionDenied(
                f"fly refused {what} (403): the token is invalid or lacks access to this app",
                status=403,
                provider=self.name,
            )
        if response.status_code == 404:
            raise NotFound(f"fly {what}: not found (404)", status=404, provider=self.name)
        if response.status_code == 429:
            wait = retry_after_seconds(response)
            raise RateLimited(
                f"fly rate limit reached for {what}; retry after "
                f"{wait if wait is not None else 1:g}s",
                retry_after=wait if wait is not None else 1.0,
                status=429,
                provider=self.name,
            )
        if response.status_code >= 400:
            raise error_from_response(self.name, response, what)
        return response.json() if response.content else {}

    async def _machine_detail(self, app: str, machine: str) -> dict[str, Any]:
        body = self._unwrap(
            await self._request("GET", f"/v1/apps/{app}/machines/{machine}"),
            "machine lookup",
        )
        return body if isinstance(body, dict) else {}

    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        if action.action_type is not ActionType.RESTART_SERVICE:
            raise ConnectorError(f"fly connector does not implement {action.action_type}")
        app = self._app_name(action.params)
        machine = self._machine(action.params)
        timeout = str(action.params.get("timeout", "30s"))
        signal = str(action.params.get("signal", "SIGTERM"))

        before = await self._machine_detail(app, machine)
        requested_at = datetime.now(UTC).isoformat()
        self._unwrap(
            await self._request(
                "POST",
                f"/v1/apps/{app}/machines/{machine}/restart",
                params={"timeout": timeout, "signal": signal},
            ),
            "machine restart",
        )
        return {
            "connector": self.name,
            "app": app,
            "machine_id": machine,
            "state_before": before.get("state"),
            "requested_at": requested_at,
            "timeout": timeout,
            "signal": signal,
        }

    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult:
        if tool != "fly.machines":
            raise ConnectorError(f"unknown fly tool {tool}")
        app = self._app_name(params)
        body = self._unwrap(
            await self._request("GET", f"/v1/apps/{app}/machines"), "machine listing"
        )
        machines = [
            {
                "id": str(item.get("id", "")),
                "state": item.get("state"),
                "region": item.get("region"),
                "updated_at": item.get("updated_at"),
                "instance_id": item.get("instance_id"),
            }
            for item in (body if isinstance(body, list) else [])
        ]
        return ToolResult(
            kind=EvidenceKind.DEPLOYMENT,
            source=f"fly:app/{app}",
            summary=f"{len(machines)} machine(s) in {app}",
            content={"app": app, "machines": machines},
        )

    def verification_checks(self) -> dict[str, CheckFn]:
        return {"fly.machine_started": self._check_machine_started}

    async def _check_machine_started(self, predicate: VerificationPredicate) -> dict[str, Any]:
        app = str(predicate.params.get("app") or self._app or "")
        machine = str(predicate.params.get("machine_id") or predicate.params.get("machine") or "")
        since = str(predicate.params.get("since") or "")
        if not app or not machine:
            raise VerificationError("fly.machine_started requires app and machine_id")
        detail = await self._machine_detail(app, machine)
        state = str(detail.get("state", ""))
        updated = str(detail.get("updated_at", ""))
        updated_ok = True
        if since:
            updated_ok = _at_or_after(updated, since)
        return {
            "app": app,
            "machine_id": machine,
            "state": state,
            "updated_at": updated,
            "since": since or None,
            "passed": state == "started" and updated_ok,
        }

    def capabilities(self) -> tuple[Capability, ...]:
        return (
            Capability(
                action_type=ActionType.RESTART_SERVICE,
                provider=self.name,
                target_kinds=("app", "machine"),
                notes="restart one machine; state and updated_at are read back",
                proof="machine state and update time",
            ),
        )

    async def preflight(self, *, deep: bool = False) -> CapabilityReport:
        started = time.monotonic()
        try:
            if self._app:
                body = await self._request("GET", f"/v1/apps/{self._app}/machines")
            else:
                body = await self._request("GET", "/v1/apps")
        except ConnectorError as exc:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=str(exc),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        latency = int((time.monotonic() - started) * 1000)
        if body.status_code in {401, 403}:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=f"token rejected ({body.status_code})",
                latency_ms=latency,
            )
        if body.status_code >= 400:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=f"fly returned {body.status_code}",
                latency_ms=latency,
            )
        payload = body.json() if body.content else []
        if self._app:
            machines = payload if isinstance(payload, list) else []
            states = sorted({str(item.get("state", "")) for item in machines})
            return CapabilityReport(
                provider=self.name,
                verdict="ok",
                identity=f"app:{self._app}",
                detail=f"{len(machines)} machine(s): {', '.join(states) or 'none'}",
                latency_ms=latency,
            )
        apps = payload if isinstance(payload, list) else []
        return CapabilityReport(
            provider=self.name,
            verdict="ok",
            identity="api token verified",
            detail=(
                f"{len(apps)} app(s) visible; set NIGHTJAR_FLY_APP to check one"
                if apps
                else "set NIGHTJAR_FLY_APP to check one app"
            ),
            latency_ms=latency,
        )


def _parse_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _at_or_after(updated: str, since: str) -> bool:
    left = _parse_timestamp(updated)
    right = _parse_timestamp(since)
    if left is None or right is None:
        return False
    return left >= right
