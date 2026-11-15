from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_token: str | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_groups_claim: str = "groups"
    require_separation_of_duties: bool = False
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://127.0.0.1:3000"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> Any:
        """Accept a JSON list or the comma-separated form used in .env.example."""
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                parsed = json.loads(text)
                if not isinstance(parsed, list):
                    raise ValueError("cors_origins JSON must be a list")
                return parsed
            return [part.strip() for part in text.split(",") if part.strip()]
        return value

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
    google_service_account_file: str | None = None
    google_api_url: str = "https://gmail.googleapis.com"
    google_oauth_url: str = "https://oauth2.googleapis.com/token"
    google_quarantine_label: str = "Nightjar-Quarantine"
    notify_webhook_url: str | None = None
    notify_webhook_secret: str | None = None

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
        if problems:
            raise ValueError("unsafe production configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
