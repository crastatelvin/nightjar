from __future__ import annotations

from typing import Any

from nightjar.api.settings import Settings
from nightjar.connectors.base import (
    CheckFn,
    Connector,
    ConnectorError,
    ManualStep,
    ToolResult,
)
from nightjar.connectors.cloudflare import CloudflareConnector
from nightjar.connectors.fly import FlyConnector
from nightjar.connectors.github import GitHubConnector
from nightjar.connectors.google_workspace import GoogleWorkspaceConnector
from nightjar.connectors.http import HttpConnector
from nightjar.connectors.lab import LabConnector
from nightjar.connectors.microsoft365 import Microsoft365Connector
from nightjar.connectors.render import RenderConnector
from nightjar.connectors.stripe import StripeConnector
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

    def manual_step(self, action: ActionSpec) -> ManualStep | None:
        """Return instructions when this action is manual at its provider.

        A connector that advertises ``automated=False`` must be able to describe
        the human step. If it cannot, the plan is not executable and the caller
        must fail closed rather than guess.
        """
        connector = self.connector_for(action)
        capability = next(
            (item for item in connector.capabilities() if item.action_type is action.action_type),
            None,
        )
        if capability is None:
            raise ConnectorError(
                f"connector {connector.name!r} implements {action.action_type.value} "
                "without advertising a capability"
            )
        if capability.automated:
            return None
        describe = getattr(connector, "manual_step", None)
        if describe is None:
            raise ConnectorError(
                f"connector {connector.name!r} advertises {action.action_type.value} as "
                "manual but cannot describe the step"
            )
        step: ManualStep = describe(action)
        return step

    def is_manual(self, action: ActionSpec) -> bool:
        """Whether this action is a human step at its provider.

        This answers the question without describing the step, so a case-file
        read does not resolve a credential from the vault. An unbound or
        ambiguous action is not claimed as manual; the plan is rejected before
        approval, and saying "manual" would invent a fact.
        """
        try:
            connector = self.connector_for(action)
        except ConnectorError:
            return False
        capability = next(
            (item for item in connector.capabilities() if item.action_type is action.action_type),
            None,
        )
        return capability is not None and not capability.automated

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


def build_registry(settings: Settings, *, include_unconfigured: bool = False) -> ConnectorRegistry:
    """Build the registry for a deployment.

    ``include_unconfigured`` exists for documentation and for tests that need to
    see the whole supported surface. It instantiates every connector with a
    placeholder credential instead of skipping the ones this deployment has not
    configured. Nothing is called, so the placeholder never reaches a provider;
    the only purpose is to ask each connector what it can do and what proof it
    produces.
    """
    placeholder = "catalogue"
    real: list[Connector] = [HttpConnector(allow_private=settings.allow_private_probes)]
    if include_unconfigured or settings.github_token or settings.github_credential_revocation:
        real.append(
            GitHubConnector(
                settings.github_token or (placeholder if include_unconfigured else None),
                base_url=settings.github_api_url,
                vault=EnvVault(),
            )
        )
    if include_unconfigured or settings.cloudflare_token:
        real.append(
            CloudflareConnector(
                settings.cloudflare_token or placeholder,
                base_url=settings.cloudflare_api_url,
                account_id=settings.cloudflare_account_id,
                pages_project=settings.cloudflare_pages_project,
            )
        )
    if include_unconfigured or settings.vercel_token:
        real.append(
            VercelConnector(
                settings.vercel_token or placeholder,
                base_url=settings.vercel_api_url,
                team_id=settings.vercel_team_id,
                project=settings.vercel_project,
            )
        )
    if include_unconfigured or settings.render_api_key:
        real.append(
            RenderConnector(
                settings.render_api_key or placeholder,
                base_url=settings.render_api_url,
                service_id=settings.render_service_id,
            )
        )
    if include_unconfigured or settings.fly_api_token:
        real.append(
            FlyConnector(
                settings.fly_api_token or placeholder,
                base_url=settings.fly_api_url,
                app=settings.fly_app,
            )
        )
    if include_unconfigured or settings.stripe_secret_key:
        real.append(
            StripeConnector(
                settings.stripe_secret_key,
                base_url=settings.stripe_api_url,
            )
        )
    if include_unconfigured or settings.google_service_account_file:
        real.append(
            GoogleWorkspaceConnector(
                settings.google_service_account_file or f"{placeholder}.json",
                mailboxes=tuple(settings.managed_targets.get("google", {}).get("mailboxes", [])),
                api_url=settings.google_api_url,
                oauth_url=settings.google_oauth_url,
                quarantine_label=settings.google_quarantine_label,
            )
        )
    if include_unconfigured or (
        settings.microsoft_tenant_id
        and settings.microsoft_client_id
        and settings.microsoft_client_secret
    ):
        real.append(
            Microsoft365Connector(
                settings.microsoft_tenant_id or placeholder,
                settings.microsoft_client_id or placeholder,
                settings.microsoft_client_secret or placeholder,
                mailboxes=tuple(settings.managed_targets.get("microsoft", {}).get("mailboxes", [])),
                graph_url=settings.microsoft_graph_url,
                login_url=settings.microsoft_login_url,
                quarantine_folder=settings.microsoft_quarantine_folder,
                propagation_seconds=settings.microsoft_propagation_seconds,
            )
        )
    if include_unconfigured or settings.notify_webhook_url:
        real.append(
            WebhookConnector(
                settings.notify_webhook_url or "https://example.invalid/nightjar",
                secret=settings.notify_webhook_secret,
            )
        )

    connectors: list[Connector] = list(real)
    if settings.lab_enabled or include_unconfigured:
        # The lab stands in only for actions no real connector implements. A
        # synthetic "credential revoked" must never shadow the real thing. The
        # documentation catalogue is the exception: there the lab is shown as a
        # provider in its own right, because that is what it is in the studio.
        claimed = (
            set()
            if include_unconfigured
            else {action for connector in real for action in connector.action_types}
        )
        connectors.insert(0, LabConnector(settings.lab_base_url, exclude_actions=claimed))
    return ConnectorRegistry(connectors)
