from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator
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
    cloudflare_token: str | None = None
    cloudflare_api_url: str = "https://api.cloudflare.com/client/v4"
    notify_webhook_url: str | None = None

    otel_enabled: bool = False
    otel_endpoint: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
