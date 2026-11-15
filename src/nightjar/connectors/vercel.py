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
from nightjar.core.enums import ActionType, EvidenceKind
from nightjar.core.models import ActionSpec, VerificationPredicate
from nightjar.core.verification import VerificationError


class VercelConnector:
    """Instant rollback and read-back checks for Vercel projects.

    A rollback response only says Vercel accepted the request. Resolution
    requires the deployment to be `READY`, targeted at production, and reachable
    through the production alias.
    """

    name = "vercel"
    action_types = frozenset({ActionType.ROLLBACK_DEPLOYMENT})
    tool_names = frozenset({"vercel.deployments"})

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.vercel.com",
        team_id: str | None = None,
        project: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._team_id = team_id or None
        self._project = project or None
        self._timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._token}"}

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = dict(extra or {})
        if self._team_id:
            params["teamId"] = self._team_id
        return params

    def _project_id(self, action: ActionSpec) -> str:
        # Precedence mirrors core/targets.py: policy validated the value named in
        # the plan, so a configured default may not override it.
        project = str(
            action.params.get("project")
            or action.params.get("project_id")
            or action.params.get("service")
            or self._project
            or ""
        )
        if not project:
            raise ConnectorError(
                "vercel rollback requires a project: set params.project or "
                "NIGHTJAR_VERCEL_PROJECT_ID"
            )
        return project

    def _deployment_id(self, action: ActionSpec) -> str:
        deployment = str(
            action.params.get("deployment_id")
            or action.params.get("target_version")
            or action.target
            or ""
        )
        if not deployment:
            raise ConnectorError("vercel rollback requires a deployment id")
        return deployment

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.request(
                    method,
                    f"{self._base}{path}",
                    headers=self._headers,
                    params=self._params(params),
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"vercel unavailable: {exc}", retryable=True) from exc

    @staticmethod
    def _unwrap(response: httpx.Response, what: str) -> Any:
        if response.status_code in {401, 403}:
            raise ConnectorError(
                f"vercel refused {what} ({response.status_code}): the token is invalid "
                "or lacks access to this project/team"
            )
        if response.status_code == 404:
            raise ConnectorError(f"vercel {what}: not found (404)")
        if response.status_code == 409:
            raise ConnectorError(f"vercel {what}: project is transferring (409)")
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after", "1")
            raise ConnectorError(
                f"vercel rate limit reached for {what}; retry after {retry_after}s",
                retryable=True,
            )
        if response.status_code >= 400:
            raise ConnectorError(
                f"vercel {what} failed: {response.status_code} {_vercel_error(response)}"
            )
        return response.json() if response.content else {}

    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        if action.action_type is not ActionType.ROLLBACK_DEPLOYMENT:
            raise ConnectorError(f"vercel connector does not implement {action.action_type}")
        project = self._project_id(action)
        deployment = self._deployment_id(action)

        # Refuse a target that is not already READY: Vercel will accept the
        # promotion and the incident then fails at verification instead of here.
        detail = await self._deployment(deployment)
        state = str(detail.get("readyState", ""))
        if state and state != "READY":
            raise ConnectorError(f"vercel rollback target {deployment} is {state}, not READY")

        body = self._unwrap(
            await self._request("POST", f"/v1/projects/{project}/rollback/{deployment}"),
            "rollback",
        )
        return {
            "connector": self.name,
            "project": project,
            "deployment_id": deployment,
            "ready_state": (body or {}).get("readyState") if isinstance(body, dict) else None,
            "url": (body or {}).get("url") if isinstance(body, dict) else None,
        }

    async def _deployment(self, deployment: str) -> dict[str, Any]:
        body = self._unwrap(
            await self._request("GET", f"/v13/deployments/{deployment}"), "deployment lookup"
        )
        return body if isinstance(body, dict) else {}

    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult:
        if tool != "vercel.deployments":
            raise ConnectorError(f"unknown vercel tool {tool}")
        project = str(params.get("project") or self._project or "")
        if not project:
            raise ConnectorError(
                "vercel.deployments requires a project (params or NIGHTJAR_VERCEL_PROJECT_ID)"
            )
        body = self._unwrap(
            await self._request(
                "GET",
                "/v6/deployments",
                params={"projectId": project, "target": "production", "limit": 10},
            ),
            "deployment listing",
        )
        deployments = [
            {
                "id": str(item.get("uid") or item.get("id") or ""),
                "url": item.get("url"),
                "ready_state": item.get("readyState"),
                "target": item.get("target"),
                "created_at": item.get("createdAt") or item.get("created"),
            }
            for item in (body.get("deployments") or [])
        ]
        return ToolResult(
            kind=EvidenceKind.DEPLOYMENT,
            source=f"vercel:project/{project}",
            summary=f"{len(deployments)} production deployment(s) for {project}",
            content={"project": project, "deployments": deployments},
        )

    def verification_checks(self) -> dict[str, CheckFn]:
        return {
            "vercel.deployment_ready": self._check_deployment_ready,
            "vercel.alias_present": self._check_alias_present,
        }

    async def _check_deployment_ready(self, predicate: VerificationPredicate) -> dict[str, Any]:
        deployment = str(predicate.params.get("deployment_id", ""))
        if not deployment:
            raise VerificationError("vercel.deployment_ready requires deployment_id")
        detail = await self._deployment(deployment)
        state = str(detail.get("readyState", ""))
        target = str(detail.get("target", ""))
        return {
            "deployment_id": deployment,
            "ready_state": state,
            "target": target,
            "passed": state == "READY" and target == "production",
        }

    async def _check_alias_present(self, predicate: VerificationPredicate) -> dict[str, Any]:
        deployment = str(predicate.params.get("deployment_id", ""))
        domain = str(predicate.params.get("domain", "")).strip().lower()
        if not deployment or not domain:
            raise VerificationError("vercel.alias_present requires deployment_id and domain")
        body = self._unwrap(
            await self._request("GET", f"/v2/deployments/{deployment}/aliases"),
            "alias check",
        )
        aliases = {
            str(item.get("alias") or item.get("domain") or "").lower()
            for item in (body.get("aliases") or [])
        }
        return {
            "deployment_id": deployment,
            "domain": domain,
            "aliases": sorted(aliases),
            "passed": domain in aliases,
        }

    def capabilities(self) -> tuple[Capability, ...]:
        return (
            Capability(
                action_type=ActionType.ROLLBACK_DEPLOYMENT,
                provider=self.name,
                target_kinds=("project",),
                notes="instant rollback to a READY production deployment; alias checked",
            ),
        )

    async def preflight(self, *, deep: bool = False) -> CapabilityReport:
        started = time.monotonic()
        try:
            response = await self._request("GET", "/v2/user")
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
                detail=f"token rejected ({response.status_code})",
                latency_ms=latency,
            )
        if response.status_code >= 400:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=f"vercel returned {response.status_code}",
                latency_ms=latency,
            )
        body = response.json() if response.content else {}
        user = body.get("user") or {}
        identity = str(user.get("username") or user.get("email") or "token verified")
        detail = f"team {self._team_id}" if self._team_id else "personal scope"
        return CapabilityReport(
            provider=self.name,
            verdict="ok",
            identity=f"user:{identity}",
            detail=detail,
            latency_ms=latency,
        )


def _vercel_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or f"HTTP {response.status_code}"
    error = body.get("error")
    if isinstance(error, dict):
        message = str(error.get("message", ""))[:200]
        code = str(error.get("code", ""))
        return f"{code}: {message}".strip(": ") or f"HTTP {response.status_code}"
    return response.text[:200] or f"HTTP {response.status_code}"
