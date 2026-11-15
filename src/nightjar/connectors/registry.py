from __future__ import annotations

from typing import Any

from nightjar.api.settings import Settings
from nightjar.connectors.base import CheckFn, Connector, ConnectorError, ToolResult
from nightjar.connectors.cloudflare import CloudflareConnector
from nightjar.connectors.github import GitHubConnector
from nightjar.connectors.google_workspace import GoogleWorkspaceConnector
from nightjar.connectors.http import HttpConnector
from nightjar.connectors.lab import LabConnector
from nightjar.connectors.render import RenderConnector
from nightjar.connectors.vercel import VercelConnector
from nightjar.connectors.webhook import WebhookConnector
from nightjar.core.actions import validate_action
from nightjar.core.enums import ActionType
from nightjar.core.models import ActionSpec
from nightjar.core.secrets import EnvVault


class ConnectorRegistry:
    """Maps typed actions and read tools to exactly one connector.

    Ambiguity is an error, not a guess. A plan can always disambiguate with
    ``params.connector``, which policy treats as part of the action specification.
    """

    def __init__(self, connectors: list[Connector]) -> None:
        self._connectors = connectors
        self._tools: dict[str, Connector] = {}
        self._checks: dict[str, CheckFn] = {}
        for connector in connectors:
            for tool in connector.tool_names:
                if tool in self._tools:
                    raise ValueError(f"duplicate tool registration: {tool}")
                self._tools[tool] = connector
            for name, check in connector.verification_checks().items():
                if name in self._checks:
                    raise ValueError(f"duplicate verification check: {name}")
                self._checks[name] = check

    @property
    def connectors(self) -> list[Connector]:
        return list(self._connectors)

    def tools(self) -> list[str]:
        return sorted(self._tools)

    def verification_checks(self) -> dict[str, CheckFn]:
        return dict(self._checks)

    def connector_for(self, action: ActionSpec) -> Connector:
        requested = action.params.get("connector")
        candidates = [
            connector
            for connector in self._connectors
            if action.action_type in connector.action_types
        ]
        if requested is not None:
            candidates = [connector for connector in candidates if connector.name == requested]
        if not candidates:
            raise ConnectorError(
                f"no connector available for {action.action_type}"
                + (f" named {requested!r}" if requested else "")
            )
        if len(candidates) > 1:
            names = sorted(connector.name for connector in candidates)
            raise ConnectorError(
                f"ambiguous connector for {action.action_type}: {names}; "
                "set params.connector explicitly"
            )
        return candidates[0]

    def providers_for(self, action_type: ActionType) -> list[str]:
        """Every connector that can perform this action, sorted by name."""
        return sorted(
            connector.name
            for connector in self._connectors
            if action_type in connector.action_types
        )

    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        validate_action(action)
        connector = self.connector_for(action)
        return await connector.execute(action)

    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult:
        connector = self._tools.get(tool)
        if connector is None:
            raise ConnectorError(f"tool {tool!r} is not in the read-only registry")
        for key, value in params.items():
            if isinstance(value, str) and len(value) > 2000:
                raise ConnectorError(f"tool parameter {key!r} is too long")
        return await connector.collect(tool, params)


def build_registry(settings: Settings) -> ConnectorRegistry:
    real: list[Connector] = [HttpConnector(allow_private=settings.allow_private_probes)]
    if settings.github_token or settings.github_credential_revocation:
        real.append(
            GitHubConnector(
                settings.github_token,
                base_url=settings.github_api_url,
                vault=EnvVault(),
            )
        )
    if settings.cloudflare_token:
        real.append(
            CloudflareConnector(
                settings.cloudflare_token,
                base_url=settings.cloudflare_api_url,
                account_id=settings.cloudflare_account_id,
                pages_project=settings.cloudflare_pages_project,
            )
        )
    if settings.vercel_token:
        real.append(
            VercelConnector(
                settings.vercel_token,
                base_url=settings.vercel_api_url,
                team_id=settings.vercel_team_id,
                project=settings.vercel_project,
            )
        )
    if settings.render_api_key:
        real.append(
            RenderConnector(
                settings.render_api_key,
                base_url=settings.render_api_url,
                service_id=settings.render_service_id,
            )
        )
    if settings.google_service_account_file:
        real.append(
            GoogleWorkspaceConnector(
                settings.google_service_account_file,
                mailboxes=tuple(settings.managed_targets.get("google", {}).get("mailboxes", [])),
                api_url=settings.google_api_url,
                oauth_url=settings.google_oauth_url,
                quarantine_label=settings.google_quarantine_label,
            )
        )
    if settings.notify_webhook_url:
        real.append(
            WebhookConnector(settings.notify_webhook_url, secret=settings.notify_webhook_secret)
        )

    connectors: list[Connector] = list(real)
    if settings.lab_enabled:
        # The lab stands in only for actions no real connector implements. A
        # synthetic "credential revoked" must never shadow the real thing.
        claimed = {action for connector in real for action in connector.action_types}
        connectors.insert(0, LabConnector(settings.lab_base_url, exclude_actions=claimed))
    return ConnectorRegistry(connectors)
