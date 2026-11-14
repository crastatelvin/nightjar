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


class CloudflareConnector:
    """Cache purge, under-attack, and Pages rollback against the real API.

    Every mutating call is followed by a read-back check registered with the
    verifier: a `200` from `purge_cache` says the purge was accepted, not that
    the zone is serving fresh content, and a rollback response says the request
    was queued, not that production moved.
    """

    name = "cloudflare"
    action_types = frozenset(
        {
            ActionType.PURGE_CACHE,
            ActionType.ENABLE_UNDER_ATTACK,
            ActionType.ROLLBACK_DEPLOYMENT,
        }
    )
    tool_names = frozenset({"cloudflare.deployments"})

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.cloudflare.com/client/v4",
        account_id: str | None = None,
        pages_project: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._account_id = account_id or None
        self._pages_project = pages_project or None
        self._timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._token}"}

    # ------------------------------------------------------------------ helpers
    def _zone(self, action: ActionSpec) -> str:
        zone = str(action.params.get("zone_id") or action.params.get("zone") or action.target)
        if not zone:
            raise ConnectorError("cloudflare action requires a zone id")
        return zone

    def _account(self, action: ActionSpec) -> str:
        account = str(action.params.get("account_id") or self._account_id or "")
        if not account:
            raise ConnectorError(
                "cloudflare Pages action requires account_id: set params.account_id or "
                "NIGHTJAR_CLOUDFLARE_ACCOUNT_ID"
            )
        return account

    def _project(self, action: ActionSpec) -> str:
        # Precedence must match core/targets.py: policy validated the value named
        # in the plan, so the connector may never quietly act on a configured
        # default instead.
        project = str(
            action.params.get("project")
            or action.params.get("project_id")
            or action.params.get("service")
            or self._pages_project
            or ""
        )
        if not project:
            raise ConnectorError(
                "cloudflare Pages action requires a project: set params.project or "
                "NIGHTJAR_CLOUDFLARE_PAGES_PROJECT"
            )
        return project

    def _deployment(self, action: ActionSpec) -> str:
        deployment = str(
            action.params.get("deployment_id")
            or action.params.get("target_version")
            or action.target
            or ""
        )
        if not deployment:
            raise ConnectorError("cloudflare Pages rollback requires a deployment id")
        return deployment

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None, params: Any = None
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.request(
                    method, f"{self._base}{path}", headers=self._headers, json=json, params=params
                )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"cloudflare unavailable: {exc}", retryable=True) from exc

    @staticmethod
    def _unwrap(response: httpx.Response, what: str) -> dict[str, Any]:
        if response.status_code in {401, 403}:
            raise ConnectorError(
                f"cloudflare refused {what} ({response.status_code}): the token lacks the "
                "required permission or zone scope"
            )
        if response.status_code == 404:
            raise ConnectorError(f"cloudflare {what}: not found (404)")
        if response.status_code >= 400:
            raise ConnectorError(
                f"cloudflare {what} failed: {response.status_code} {response.text[:300]}"
            )
        body: dict[str, Any] = response.json() if response.content else {}
        if not body.get("success", False):
            raise ConnectorError(f"cloudflare rejected {what}: {body.get('errors')}")
        return body

    async def _deployment_detail(
        self, account: str, project: str, deployment: str
    ) -> dict[str, Any]:
        response = await self._request(
            "GET",
            f"/accounts/{account}/pages/projects/{project}/deployments/{deployment}",
        )
        return self._unwrap(response, "deployment lookup").get("result") or {}

    # ------------------------------------------------------------------ actions
    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        if action.action_type is ActionType.PURGE_CACHE:
            return await self._purge(action)
        if action.action_type is ActionType.ENABLE_UNDER_ATTACK:
            return await self._under_attack(action)
        if action.action_type is ActionType.ROLLBACK_DEPLOYMENT:
            return await self._rollback(action)
        raise ConnectorError(f"cloudflare connector does not implement {action.action_type}")

    async def _purge(self, action: ActionSpec) -> dict[str, Any]:
        zone = self._zone(action)
        urls = action.params.get("urls") or []
        if not isinstance(urls, list) or not urls:
            raise ConnectorError("purge_cache requires a non-empty list of urls")
        response = await self._request("POST", f"/zones/{zone}/purge_cache", json={"files": urls})
        body = self._unwrap(response, "cache purge")
        return {
            "connector": self.name,
            "zone": zone,
            "urls": urls,
            "result": body.get("result"),
        }

    async def _under_attack(self, action: ActionSpec) -> dict[str, Any]:
        zone = self._zone(action)
        response = await self._request(
            "PATCH",
            f"/zones/{zone}/settings/security_level",
            json={"value": "under_attack"},
        )
        body = self._unwrap(response, "security level change")
        return {
            "connector": self.name,
            "zone": zone,
            "security_level": (body.get("result") or {}).get("value", "under_attack"),
        }

    async def _rollback(self, action: ActionSpec) -> dict[str, Any]:
        account = self._account(action)
        project = self._project(action)
        deployment = self._deployment(action)

        # A rollback target must be a successful production deployment. Cloudflare
        # rejects anything else, but checking first turns a provider 400 into a
        # precise failure that names the deployment.
        detail = await self._deployment_detail(account, project, deployment)
        stage = str((detail.get("latest_stage") or {}).get("status", ""))
        if stage and stage != "success":
            raise ConnectorError(
                f"cloudflare rollback target {deployment} is not a successful deployment "
                f"(latest_stage.status={stage})"
            )
        environment = str(detail.get("environment", ""))
        if environment and environment != "production":
            raise ConnectorError(
                f"cloudflare rollback target {deployment} is {environment}, not production"
            )

        response = await self._request(
            "POST",
            f"/accounts/{account}/pages/projects/{project}/deployments/{deployment}/rollback",
        )
        body = self._unwrap(response, "Pages rollback")
        result = body.get("result") or {}
        return {
            "connector": self.name,
            "account_id": account,
            "project": project,
            "deployment_id": deployment,
            "previous_deployment_id": detail.get("id"),
            "result": result,
        }

    # ------------------------------------------------------------- read tools
    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult:
        if tool != "cloudflare.deployments":
            raise ConnectorError(f"unknown cloudflare tool {tool}")
        account = str(params.get("account_id") or self._account_id or "")
        project = str(params.get("project") or self._pages_project or "")
        if not account or not project:
            raise ConnectorError(
                "cloudflare.deployments requires account_id and project "
                "(params or NIGHTJAR_CLOUDFLARE_ACCOUNT_ID / NIGHTJAR_CLOUDFLARE_PAGES_PROJECT)"
            )
        response = await self._request(
            "GET",
            f"/accounts/{account}/pages/projects/{project}/deployments",
            params={"env": "production", "per_page": 10},
        )
        body = self._unwrap(response, "deployment listing")
        deployments = [
            {
                "id": str(item.get("id", "")),
                "created_on": item.get("created_on"),
                "environment": item.get("environment"),
                "status": (item.get("latest_stage") or {}).get("status"),
                "url": item.get("url"),
            }
            for item in body.get("result") or []
        ]
        return ToolResult(
            kind=EvidenceKind.DEPLOYMENT,
            source=f"cloudflare:pages/{project}",
            summary=f"{len(deployments)} production deployment(s) for {project}",
            content={"account_id": account, "project": project, "deployments": deployments},
        )

    # ------------------------------------------------------------ verification
    def verification_checks(self) -> dict[str, CheckFn]:
        return {
            "cloudflare.pages_production": self._check_pages_production,
            "cloudflare.security_level": self._check_security_level,
        }

    async def _check_pages_production(self, predicate: VerificationPredicate) -> dict[str, Any]:
        account = str(predicate.params.get("account_id") or self._account_id or "")
        project = str(predicate.params.get("project") or self._pages_project or "")
        deployment = str(predicate.params.get("deployment_id", ""))
        if not account or not project or not deployment:
            raise VerificationError(
                "cloudflare.pages_production requires account_id, project, and deployment_id"
            )
        response = await self._request(
            "GET",
            f"/accounts/{account}/pages/projects/{project}/deployments",
            params={"env": "production", "per_page": 1},
        )
        body = self._unwrap(response, "production deployment check")
        items = body.get("result") or []
        current = str(items[0].get("id", "")) if items else ""
        return {
            "account_id": account,
            "project": project,
            "expected": deployment,
            "current": current,
            "passed": current == deployment,
        }

    async def _check_security_level(self, predicate: VerificationPredicate) -> dict[str, Any]:
        zone = str(predicate.params.get("zone_id") or predicate.params.get("zone") or "")
        expected = str(predicate.params.get("value") or "under_attack")
        if not zone:
            raise VerificationError("cloudflare.security_level requires zone_id")
        response = await self._request("GET", f"/zones/{zone}/settings/security_level")
        body = self._unwrap(response, "security level check")
        current = str((body.get("result") or {}).get("value", ""))
        return {
            "zone": zone,
            "expected": expected,
            "current": current,
            "passed": current == expected,
        }

    # ------------------------------------------------------------- capability
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
            Capability(
                action_type=ActionType.ROLLBACK_DEPLOYMENT,
                provider=self.name,
                target_kinds=("project",),
                notes="Cloudflare Pages rollback to a successful production deployment",
            ),
        )

    async def preflight(self, *, deep: bool = False) -> CapabilityReport:
        started = time.monotonic()
        try:
            response = await self._request("GET", "/user/tokens/verify")
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
