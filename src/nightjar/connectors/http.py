from __future__ import annotations

from typing import Any

import httpx

from nightjar.connectors.base import Capability, CapabilityReport, ConnectorError, ToolResult
from nightjar.core.enums import ActionType, EvidenceKind
from nightjar.core.models import ActionSpec
from nightjar.core.verification import validate_url


class HttpConnector:
    """Read-only HTTP evidence collection with the verifier's SSRF guard."""

    name = "http"
    action_types: frozenset[ActionType] = frozenset()
    tool_names = frozenset({"http.get"})

    def __init__(self, *, allow_private: bool = False, timeout: float = 10.0) -> None:
        self._allow_private = allow_private
        self._timeout = timeout

    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        raise ConnectorError("http connector is read-only")

    def capabilities(self) -> tuple[Capability, ...]:
        # A read-only prober performs no action, so it advertises none.
        return ()

    async def preflight(self, *, deep: bool = False) -> CapabilityReport:
        return CapabilityReport(
            provider=self.name,
            verdict="ok",
            identity="read-only evidence prober",
            detail=(
                "private targets allowed (lab only)"
                if self._allow_private
                else "public targets only"
            ),
        )

    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult:
        if tool != "http.get":
            raise ConnectorError(f"unknown http tool {tool}")
        url = str(params.get("url", ""))
        try:
            validate_url(url, allow_private=self._allow_private)
        except Exception as exc:
            raise ConnectorError(str(exc)) from exc
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
                headers={"user-agent": "nightjar-investigator/0.1"},
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"request failed: {exc}", retryable=True) from exc
        content_type = response.headers.get("content-type", "")
        body = response.text[:4000]
        return ToolResult(
            kind=EvidenceKind.HTTP_RESPONSE,
            source=url,
            summary=f"GET {url} returned {response.status_code}",
            content={
                "url": url,
                "status": response.status_code,
                "content_type": content_type,
                "headers": {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower()
                    in {"server", "cache-control", "x-cache", "x-version", "content-type"}
                },
                "body_excerpt": body if "json" in content_type or "text" in content_type else "",
                "truncated": len(response.text) > 4000,
            },
        )

    def verification_checks(self) -> dict[str, Any]:
        return {}
