from __future__ import annotations

import time
from typing import Any

import httpx

from nightjar.connectors.base import Capability, CapabilityReport, ConnectorError, ToolResult
from nightjar.core.enums import ActionType, EvidenceKind
from nightjar.core.models import ActionSpec, VerificationPredicate
from nightjar.core.secrets import (
    EnvVault,
    SecretError,
    SecretVault,
    fingerprint,
    hint,
    is_github_credential,
)
from nightjar.core.verification import VerificationError

REVOCATION_ACCEPT = "application/vnd.github+json"
REVOCATION_API_VERSION = "2026-03-10"


class GitHubConnector:
    """Real GitHub connector: issues, repository reads, and token revocation.

    Revocation uses GitHub's unauthenticated credential revocation API. A ``202``
    means the request was accepted, not that the token was valid or is now dead,
    so the connector never reports revocation as verified. The verifier proves it
    by presenting the token and expecting a ``401``.
    """

    name = "github"
    action_types = frozenset({ActionType.OPEN_ISSUE, ActionType.REVOKE_CREDENTIAL})
    tool_names = frozenset({"github.repo_file"})

    def __init__(
        self,
        token: str | None,
        *,
        base_url: str = "https://api.github.com",
        vault: SecretVault | None = None,
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._vault = vault or EnvVault()

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._token}",
            "accept": REVOCATION_ACCEPT,
            "x-github-api-version": "2022-11-28",
        }

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                return await client.request(
                    method, f"{self._base}{path}", headers=self._headers(), **kwargs
                )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"github unavailable: {exc}", retryable=True) from exc

    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        if action.action_type is ActionType.REVOKE_CREDENTIAL:
            return await self._revoke(action)
        if action.action_type is not ActionType.OPEN_ISSUE:
            raise ConnectorError(f"github connector does not implement {action.action_type}")
        if not self._token:
            raise ConnectorError("github issue creation requires NIGHTJAR_GITHUB_TOKEN")
        repository = str(action.params.get("repository") or action.target)
        response = await self._request(
            "POST",
            f"/repos/{repository}/issues",
            json={
                "title": action.params.get("title", "Nightjar incident"),
                "body": action.params.get("body", ""),
                "labels": action.params.get("labels", ["nightjar"]),
            },
        )
        if response.status_code >= 400:
            raise ConnectorError(
                f"github issue creation failed: {response.status_code} {response.text[:300]}"
            )
        body = response.json()
        return {"issue_url": body.get("html_url"), "number": body.get("number")}

    async def _revoke(self, action: ActionSpec) -> dict[str, Any]:
        ref = str(action.params.get("credential_ref", ""))
        if not ref:
            raise ConnectorError(
                "revoke_credential requires credential_ref (env:NAME or file:/path)"
            )
        try:
            raw = self._vault.resolve(ref)
        except SecretError as exc:
            raise ConnectorError(str(exc)) from exc
        if not is_github_credential(raw):
            raise ConnectorError(
                "credential is not a GitHub token (expected ghp_, github_pat_, gho_, ghu_, or ghr_)"
            )
        if "api.github.com" not in self._base:
            raise ConnectorError(
                "GitHub Enterprise Server revocation uses a different, authenticated "
                "contract and is not supported in v1"
            )

        # Unauthenticated on purpose: authenticated requests to this endpoint
        # return 403.
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self._base}/credentials/revoke",
                    headers={
                        "accept": REVOCATION_ACCEPT,
                        "x-github-api-version": REVOCATION_API_VERSION,
                    },
                    json={"credentials": [raw]},
                )
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"github revocation request failed: {exc}", retryable=True
            ) from exc

        if response.status_code == 429:
            raise ConnectorError(
                "github revocation rate limit reached (60 unauthenticated requests/hour)",
                retryable=True,
            )
        if response.status_code == 403:
            raise ConnectorError(
                "github revocation rejected the request as authenticated; "
                "this endpoint must be called without a token"
            )
        if response.status_code != 202:
            raise ConnectorError(
                f"github revocation failed: {response.status_code} {response.text[:200]}"
            )

        # 202 only means "queued". Never claim the credential is dead here.
        return {
            "provider": "github",
            "requested": True,
            "status": response.status_code,
            "fingerprint": fingerprint(raw),
            "hint": hint(raw),
            "note": "revocation accepted; liveness is proven by the verifier",
        }

    def capabilities(self) -> tuple[Capability, ...]:
        capabilities = [
            Capability(
                action_type=ActionType.REVOKE_CREDENTIAL,
                provider=self.name,
                target_kinds=("credential",),
                notes="unauthenticated revocation API; GHES unsupported in v1",
            )
        ]
        if self._token:
            capabilities.append(
                Capability(
                    action_type=ActionType.OPEN_ISSUE,
                    provider=self.name,
                    target_kinds=("repository",),
                )
            )
        return tuple(capabilities)

    async def preflight(self, *, deep: bool = False) -> CapabilityReport:
        """Prove the configured token, or that revocation-only mode is usable."""
        if not self._token:
            return CapabilityReport(
                provider=self.name,
                verdict="ok",
                identity="unauthenticated revocation only",
                detail="no token configured, so issue creation is unavailable",
            )
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(f"{self._base}/user", headers=self._headers())
        except httpx.HTTPError as exc:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=f"github unreachable: {exc}",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        latency = int((time.monotonic() - started) * 1000)
        if response.status_code == 401:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail="NIGHTJAR_GITHUB_TOKEN was rejected (401)",
                latency_ms=latency,
            )
        if response.status_code == 403:
            body = response.text[:200]
            return CapabilityReport(
                provider=self.name,
                verdict="degraded",
                detail=f"token accepted but forbidden (403): {body}",
                missing_scopes=("issues:write",),
                latency_ms=latency,
            )
        if response.status_code >= 400:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                detail=f"github returned {response.status_code}",
                latency_ms=latency,
            )
        login = str(response.json().get("login", "unknown"))
        scopes = response.headers.get("x-oauth-scopes", "")
        return CapabilityReport(
            provider=self.name,
            verdict="ok",
            identity=f"token:{login}",
            detail=f"scopes: {scopes}" if scopes else "fine-grained token",
            latency_ms=latency,
        )

    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult:
        if tool != "github.repo_file":
            raise ConnectorError(f"unknown github tool {tool}")
        repository = str(params.get("repository", ""))
        path = str(params.get("path", ""))
        if not repository or not path:
            raise ConnectorError("github.repo_file requires repository and path")
        if not self._token:
            raise ConnectorError("github reads require NIGHTJAR_GITHUB_TOKEN")
        response = await self._request("GET", f"/repos/{repository}/contents/{path}")
        if response.status_code == 404:
            return ToolResult(
                kind=EvidenceKind.SECRET_SCAN,
                source=f"github:{repository}/{path}",
                summary=f"{path} not found in {repository}",
                content={"repository": repository, "path": path, "found": False},
            )
        if response.status_code >= 400:
            raise ConnectorError(f"github read failed: {response.status_code}")
        body = response.json()
        import base64

        raw = base64.b64decode(body.get("content", "")).decode("utf-8", errors="replace")
        return ToolResult(
            kind=EvidenceKind.SECRET_SCAN,
            source=f"github:{repository}/{path}",
            summary=f"Read {path} from {repository}",
            content={
                "repository": repository,
                "path": path,
                "found": True,
                "sha": body.get("sha"),
                "excerpt": raw[:4000],
            },
        )

    def verification_checks(self) -> dict[str, Any]:
        return {"github.credential_revoked": self._check_credential_revoked}

    async def _check_credential_revoked(self, predicate: VerificationPredicate) -> dict[str, Any]:
        ref = str(predicate.params.get("credential_ref", ""))
        if not ref:
            raise VerificationError("github.credential_revoked requires credential_ref")
        try:
            raw = self._vault.resolve(ref)
        except SecretError as exc:
            raise VerificationError(str(exc)) from exc
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self._base}/user",
                    headers={
                        "authorization": f"Bearer {raw}",
                        "accept": REVOCATION_ACCEPT,
                        "x-github-api-version": "2022-11-28",
                    },
                )
        except httpx.HTTPError as exc:
            raise VerificationError(f"github credential probe failed: {exc}") from exc
        return {
            "provider": "github",
            "status": response.status_code,
            "fingerprint": fingerprint(raw),
            "passed": response.status_code == 401,
        }
