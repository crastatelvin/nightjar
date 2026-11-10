from __future__ import annotations

from typing import Any

import httpx

from nightjar.connectors.base import ConnectorError, ToolResult
from nightjar.core.enums import ActionType, EvidenceKind
from nightjar.core.models import ActionSpec


class GitHubConnector:
    """Real GitHub read/issue connector, enabled only when a token is present."""

    name = "github"
    action_types = frozenset({ActionType.OPEN_ISSUE})
    tool_names = frozenset({"github.repo_file"})

    def __init__(self, token: str, *, base_url: str = "https://api.github.com") -> None:
        self._token = token
        self._base = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._token}",
            "accept": "application/vnd.github+json",
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
        if action.action_type is not ActionType.OPEN_ISSUE:
            raise ConnectorError(f"github connector does not implement {action.action_type}")
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

    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult:
        if tool != "github.repo_file":
            raise ConnectorError(f"unknown github tool {tool}")
        repository = str(params.get("repository", ""))
        path = str(params.get("path", ""))
        if not repository or not path:
            raise ConnectorError("github.repo_file requires repository and path")
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
        return {}
