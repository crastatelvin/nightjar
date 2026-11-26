from __future__ import annotations

import time
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


class RenderConnector:
    """Render rollback and restart, including the autodeploy trap.

    An API rollback does not disable autodeploys: the next push can reintroduce
    the bad commit minutes later. A rollback therefore pauses autodeploy by
    default and reports it, and resolution requires both the target deploy to be
    live and autodeploy to still be off.
    """

    name = "render"
    action_types = frozenset({ActionType.ROLLBACK_DEPLOYMENT, ActionType.RESTART_SERVICE})
    tool_names = frozenset({"render.service", "render.deploys"})

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.render.com/v1",
        service_id: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._service_id = service_id or None
        self._timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._key}"}

    def _service(self, params: dict[str, Any]) -> str:
        # Precedence mirrors core/targets.py: the plan value wins over config.
        service = str(params.get("service_id") or params.get("service") or self._service_id or "")
        if not service:
            raise ConnectorError(
                "render action requires a service: set params.service or NIGHTJAR_RENDER_SERVICE_ID"
            )
        return service

    def _deploy(self, params: dict[str, Any]) -> str:
        deploy = str(
            params.get("deploy_id")
            or params.get("target_version")
            or params.get("deployment_id")
            or ""
        )
        if not deploy:
            raise ConnectorError("render rollback requires a deploy id")
        return deploy

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
                f"render refused {what} (401): the API key is invalid",
                status=401,
                provider=self.name,
            )
        if response.status_code == 403:
            raise PermissionDenied(
                f"render refused {what} (403): the API key is invalid or lacks the required scope",
                status=403,
                provider=self.name,
            )
        if response.status_code == 404:
            raise NotFound(f"render {what}: not found (404)", status=404, provider=self.name)
        if response.status_code == 429:
            wait = retry_after_seconds(response)
            raise RateLimited(
                f"render rate limit reached for {what}; retry after "
                f"{wait if wait is not None else 1:g}s",
                retry_after=wait if wait is not None else 1.0,
                status=429,
                provider=self.name,
            )
        if response.status_code >= 400:
            raise error_from_response(self.name, response, what)
        return response.json() if response.content else {}

    async def _service_detail(self, service: str) -> dict[str, Any]:
        body = self._unwrap(await self._request("GET", f"/services/{service}"), "service lookup")
        return body if isinstance(body, dict) else {}

    async def _deploy_detail(self, service: str, deploy: str) -> dict[str, Any]:
        body = self._unwrap(
            await self._request("GET", f"/services/{service}/deploys/{deploy}"),
            "deploy lookup",
        )
        return body if isinstance(body, dict) else {}

    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        if action.action_type is ActionType.ROLLBACK_DEPLOYMENT:
            return await self._rollback(action)
        if action.action_type is ActionType.RESTART_SERVICE:
            return await self._restart(action)
        raise ConnectorError(f"render connector does not implement {action.action_type}")

    async def _rollback(self, action: ActionSpec) -> dict[str, Any]:
        service = self._service(action.params)
        deploy = self._deploy(action.params)
        pause = bool(action.params.get("pause_autodeploy", True))

        detail = await self._service_detail(service)
        previous = str(detail.get("autoDeploy", ""))
        paused = False
        if pause and previous != "no":
            self._unwrap(
                await self._request("PATCH", f"/services/{service}", json={"autoDeploy": "no"}),
                "autodeploy pause",
            )
            paused = True

        body = self._unwrap(
            await self._request("POST", f"/services/{service}/rollback", json={"deployId": deploy}),
            "rollback",
        )
        result = body if isinstance(body, dict) else {}
        return {
            "connector": self.name,
            "service_id": service,
            "deploy_id": str(result.get("id") or deploy),
            "status": result.get("status"),
            "autodeploy_paused": paused,
            "previous_autodeploy": previous or None,
        }

    async def _restart(self, action: ActionSpec) -> dict[str, Any]:
        service = self._service(action.params)
        body = self._unwrap(await self._request("POST", f"/services/{service}/restart"), "restart")
        result = body if isinstance(body, dict) else {}
        return {
            "connector": self.name,
            "service_id": service,
            "deploy_id": result.get("id"),
            "status": result.get("status"),
        }

    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult:
        service = self._service(params)
        if tool == "render.service":
            detail = await self._service_detail(service)
            return ToolResult(
                kind=EvidenceKind.DEPLOYMENT,
                source=f"render:service/{service}",
                summary=f"Service {detail.get('name', service)} state",
                content={
                    "service_id": service,
                    "name": detail.get("name"),
                    "type": detail.get("type"),
                    "suspended": detail.get("suspended"),
                    "auto_deploy": detail.get("autoDeploy"),
                    "url": detail.get("serviceDetails", {}).get("url")
                    if isinstance(detail.get("serviceDetails"), dict)
                    else None,
                },
            )
        if tool == "render.deploys":
            body = self._unwrap(
                await self._request("GET", f"/services/{service}/deploys", params={"limit": 10}),
                "deploy listing",
            )
            items = body if isinstance(body, list) else body.get("deploys") or []
            deploys = [
                {
                    "id": str(item.get("deploy", item).get("id", "")),
                    "status": item.get("deploy", item).get("status"),
                    "commit": (item.get("deploy", item).get("commit") or {}).get("id"),
                    "created_at": item.get("deploy", item).get("createdAt"),
                }
                for item in items
            ]
            return ToolResult(
                kind=EvidenceKind.DEPLOYMENT,
                source=f"render:service/{service}",
                summary=f"{len(deploys)} deploy(s) for {service}",
                content={"service_id": service, "deploys": deploys},
            )
        raise ConnectorError(f"unknown render tool {tool}")

    def verification_checks(self) -> dict[str, CheckFn]:
        return {
            "render.deploy_live": self._check_deploy_live,
            "render.autodeploy_disabled": self._check_autodeploy_disabled,
            "render.service_not_suspended": self._check_service_not_suspended,
        }

    async def _check_deploy_live(self, predicate: VerificationPredicate) -> dict[str, Any]:
        service = str(predicate.params.get("service_id") or predicate.params.get("service") or "")
        deploy = str(
            predicate.params.get("deploy_id") or predicate.params.get("deployment_id") or ""
        )
        expected_commit = str(predicate.params.get("commit_id") or "")
        if not service or not deploy:
            raise VerificationError("render.deploy_live requires service_id and deploy_id")
        detail = await self._deploy_detail(service, deploy)
        status = str(detail.get("status", ""))
        commit = str((detail.get("commit") or {}).get("id", ""))
        commit_ok = not expected_commit or commit == expected_commit
        return {
            "service_id": service,
            "deploy_id": deploy,
            "status": status,
            "commit": commit,
            "expected_commit": expected_commit or None,
            "passed": status == "live" and commit_ok,
        }

    async def _check_autodeploy_disabled(self, predicate: VerificationPredicate) -> dict[str, Any]:
        service = str(predicate.params.get("service_id") or predicate.params.get("service") or "")
        if not service:
            raise VerificationError("render.autodeploy_disabled requires service_id")
        detail = await self._service_detail(service)
        auto = str(detail.get("autoDeploy", ""))
        return {"service_id": service, "auto_deploy": auto, "passed": auto == "no"}

    async def _check_service_not_suspended(
        self, predicate: VerificationPredicate
    ) -> dict[str, Any]:
        service = str(predicate.params.get("service_id") or predicate.params.get("service") or "")
        if not service:
            raise VerificationError("render.service_not_suspended requires service_id")
        detail = await self._service_detail(service)
        suspended = str(detail.get("suspended", ""))
        return {
            "service_id": service,
            "suspended": suspended,
            "passed": suspended in {"", "not_suspended"},
        }

    def capabilities(self) -> tuple[Capability, ...]:
        return (
            Capability(
                action_type=ActionType.ROLLBACK_DEPLOYMENT,
                provider=self.name,
                target_kinds=("service",),
                notes="rollback to a live deploy; autodeploy paused by default",
                proof="deploy state and autodeploy flag",
            ),
            Capability(
                action_type=ActionType.RESTART_SERVICE,
                provider=self.name,
                target_kinds=("service",),
                notes="zero-downtime deploy of the same commit",
                proof="deploy state and autodeploy flag",
            ),
        )

    async def preflight(self, *, deep: bool = False) -> CapabilityReport:
        started = time.monotonic()
        try:
            if self._service_id:
                detail = await self._service_detail(self._service_id)
                return CapabilityReport(
                    provider=self.name,
                    verdict="ok",
                    identity=f"service:{detail.get('name') or self._service_id}",
                    detail=(
                        "autodeploy is off"
                        if detail.get("autoDeploy") == "no"
                        else "autodeploy is on"
                    ),
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            response = await self._request("GET", "/services", params={"limit": 1})
        except ConnectorError as exc:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=str(exc),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        latency = int((time.monotonic() - started) * 1000)
        if response.status_code in {401, 403}:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=f"api key rejected ({response.status_code})",
                latency_ms=latency,
            )
        if response.status_code >= 400:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=f"render returned {response.status_code}",
                latency_ms=latency,
            )
        return CapabilityReport(
            provider=self.name,
            verdict="ok",
            identity="api key verified",
            detail="set NIGHTJAR_RENDER_SERVICE_ID to check one service",
            latency_ms=latency,
        )
