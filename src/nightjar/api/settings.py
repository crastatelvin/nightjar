from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _parse_str_list(value: Any, *, field: str) -> Any:
    """Accept a JSON list or the comma-separated form used in .env.example."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise ValueError(f"{field} JSON must be a list")
            return parsed
        return [part.strip() for part in text.split(",") if part.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NIGHTJAR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    tenant_id: str = "00000000-0000-0000-0000-000000000001"
    tenant_name: str = "Local Nightjar"

    database_url: str = "postgresql+asyncpg://nightjar@127.0.0.1:55432/nightjar"
    db_echo: bool = False
    # Advisory locks make two API replicas safe. Off is for single-process dev
    # and for tests that build a service without a lock engine.
    db_locks: bool = True

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_token: str | None = None
    # The API's own budget, separate from the per-provider connector budgets.
    # A caller gets `per_minute` tokens per minute and may spend up to `burst`
    # of them at once. Process-local: N replicas means N times this budget.
    api_rate_limit_enabled: bool = True
    api_rate_limit_per_minute: int = Field(default=120, ge=1)
    api_rate_limit_burst: int = Field(default=60, ge=1)
    # Only trust X-Forwarded-For when a proxy you control sets it; otherwise a
    # caller can pick its own address and its own budget.
    api_trust_forwarded_for: bool = False
    # Cap on any request body. The signed intake has a smaller cap of its own.
    api_max_body_bytes: int = Field(default=1_048_576, ge=1024)
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_groups_claim: str = "groups"
    # Group names whose members may sign off on an R2 action or attest a manual
    # step. Empty means any authenticated caller may; a deployment that lists
    # groups has decided that "holds a credential" is not the same as "may approve".
    oidc_approver_groups: Annotated[list[str], NoDecode] = Field(default_factory=list)
    require_separation_of_duties: bool = False
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://127.0.0.1:3000"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> Any:
        """Accept a JSON list or the comma-separated form used in .env.example."""
        return _parse_str_list(value, field="cors_origins")

    @field_validator("oidc_approver_groups", mode="before")
    @classmethod
    def parse_approver_groups(cls, value: Any) -> Any:
        """Accept a JSON list or the comma-separated form used in .env.example."""
        return _parse_str_list(value, field="oidc_approver_groups")

    @field_validator("managed_targets", mode="before")
    @classmethod
    def parse_managed_targets(cls, value: Any) -> Any:
        """Accept a JSON object, or an empty string meaning "unmanaged"."""
        if value is None:
            return {}
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("managed_targets must be a JSON object keyed by provider")
            return parsed
        return value

    orchestrator: Literal["inline", "temporal"] = "inline"
    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "nightjar"

    opa_url: str | None = None
    allow_private_probes: bool = True

    # Model provider selection. "mock" keeps the whole product runnable with no
    # API key and is what the eval harness uses in CI.
    model_provider: Literal["mock", "openai", "anthropic", "google", "ollama"] = "mock"
    model_name: str = "gpt-5.4-mini"
    model_api_key: str | None = None
    model_base_url: str | None = None
    critic_provider: Literal["mock", "openai", "anthropic", "google", "ollama"] = "mock"
    critic_model_name: str = "gpt-5.4-mini"
    critic_api_key: str | None = None
    critic_base_url: str | None = None

    max_investigation_tool_calls: int = 20
    max_investigation_seconds: int = 360
    max_cost_usd: float = 0.75
    approval_ttl_seconds: int = 1800
    stability_seconds: int = 900
    # A connector call is retried only when repeating it cannot duplicate a
    # mutation, and never waits longer than this even if the provider asks.
    connector_max_attempts: int = 3
    connector_retry_max_wait_seconds: float = 30.0

    lab_base_url: str = "http://127.0.0.1:8100"
    lab_enabled: bool = True

    github_token: str | None = None
    github_api_url: str = "https://api.github.com"
    github_org: str | None = None
    github_credential_revocation: bool = False
    cloudflare_token: str | None = None
    cloudflare_api_url: str = "https://api.cloudflare.com/client/v4"
    cloudflare_account_id: str | None = None
    cloudflare_pages_project: str | None = None
    vercel_token: str | None = None
    vercel_api_url: str = "https://api.vercel.com"
    vercel_team_id: str | None = None
    vercel_project: str | None = None
    render_api_key: str | None = None
    render_api_url: str = "https://api.render.com/v1"
    render_service_id: str | None = None
    fly_api_token: str | None = None
    fly_api_url: str = "https://api.machines.dev"
    fly_app: str | None = None
    stripe_secret_key: str | None = None
    stripe_api_url: str = "https://api.stripe.com"
    google_service_account_file: str | None = None
    google_api_url: str = "https://gmail.googleapis.com"
    google_oauth_url: str = "https://oauth2.googleapis.com/token"
    google_quarantine_label: str = "Nightjar-Quarantine"
    microsoft_tenant_id: str | None = None
    microsoft_client_id: str | None = None
    microsoft_client_secret: str | None = None
    microsoft_graph_url: str = "https://graph.microsoft.com/v1.0"
    microsoft_login_url: str = "https://login.microsoftonline.com"
    microsoft_quarantine_folder: str = "Nightjar Quarantine"
    microsoft_propagation_seconds: int = Field(default=180, ge=0)
    notify_webhook_url: str | None = None
    notify_webhook_secret: str | None = None

    # Inbound alert intake. Unset disables POST /v1/intake/webhook (503) rather
    # than leaving an unauthenticated way to file an incident.
    intake_webhook_secret: str | None = None
    intake_webhook_tolerance_seconds: int = 300
    intake_max_body_bytes: int = 65_536

    # Case-file streams wake on PostgreSQL LISTEN/NOTIFY instead of polling. The
    # poll interval is the fallback when the listener has not attached yet or the
    # database cannot notify; a missed notification costs latency, never an event.
    sse_notify_enabled: bool = True
    sse_poll_seconds: float = Field(default=5.0, ge=0.25)

    # Targets the agent may touch, keyed by provider. Policy denies anything
    # outside this list, so a prompt-injected plan cannot name its own zone,
    # repository, mailbox, project, or app. Required in production.
    managed_targets: Annotated[dict[str, dict[str, list[str]]], NoDecode] = Field(
        default_factory=dict
    )

    otel_enabled: bool = False
    otel_endpoint: str | None = None

    @model_validator(mode="after")
    def _refuse_unsafe_production(self) -> Settings:
        """Fail at startup, not at the first incident.

        Every one of these settings is safe in development and dangerous in
        production: a lab connector that pretends to revoke credentials, a
        prober that can reach link-local metadata, an unauthenticated API, and
        an agent with no target allowlist.
        """
        configured = [
            self.microsoft_tenant_id,
            self.microsoft_client_id,
            self.microsoft_client_secret,
        ]
        if any(configured) and not all(configured):
            raise ValueError(
                "Microsoft 365 needs NIGHTJAR_MICROSOFT_TENANT_ID, "
                "NIGHTJAR_MICROSOFT_CLIENT_ID, and NIGHTJAR_MICROSOFT_CLIENT_SECRET together"
            )
        if self.oidc_issuer and not self.oidc_audience:
            # An issuer with no audience check accepts a token minted for any other
            # application on the same issuer, and one with no verifier accepts
            # everything. Refuse the configuration in every environment.
            raise ValueError("NIGHTJAR_OIDC_AUDIENCE is required when NIGHTJAR_OIDC_ISSUER is set")
        if self.oidc_approver_groups and not self.oidc_issuer:
            # Groups only exist in a verified token. Configuring them without OIDC
            # would leave every approval refused, which is a lockout wearing the
            # costume of a policy.
            raise ValueError(
                "NIGHTJAR_OIDC_APPROVER_GROUPS requires NIGHTJAR_OIDC_ISSUER: "
                "the shared token carries no group claim"
            )
        if self.environment != "production":
            return self
        problems: list[str] = []
        if self.lab_enabled:
            problems.append("NIGHTJAR_LAB_ENABLED must be false in production")
        if self.allow_private_probes:
            problems.append("NIGHTJAR_ALLOW_PRIVATE_PROBES must be false in production")
        if not (self.api_token or self.oidc_issuer):
            problems.append("production requires NIGHTJAR_API_TOKEN or NIGHTJAR_OIDC_ISSUER")
        if not self.managed_targets:
            problems.append("production requires NIGHTJAR_MANAGED_TARGETS")
        if self.stability_seconds < 60:
            problems.append("NIGHTJAR_STABILITY_SECONDS must be at least 60 in production")
        if self.google_service_account_file and not self.managed_targets.get("google", {}).get(
            "mailboxes"
        ):
            problems.append(
                "NIGHTJAR_MANAGED_TARGETS.google.mailboxes is required when the "
                "Google Workspace connector is configured"
            )
        if self.intake_webhook_secret and len(self.intake_webhook_secret) < 32:
            problems.append(
                "NIGHTJAR_INTAKE_WEBHOOK_SECRET must be at least 32 characters in production"
            )
        if self.microsoft_tenant_id and not self.managed_targets.get("microsoft", {}).get(
            "mailboxes"
        ):
            problems.append(
                "NIGHTJAR_MANAGED_TARGETS.microsoft.mailboxes is required when the "
                "Microsoft 365 connector is configured"
            )
        if problems:
            raise ValueError("unsafe production configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
