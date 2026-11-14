from __future__ import annotations

import pytest
from pydantic import ValidationError

from nightjar.api.settings import Settings


def _production(**overrides: object) -> Settings:
    """A production configuration that passes, so each test can break one field."""
    base: dict[str, object] = {
        "environment": "production",
        "lab_enabled": False,
        "allow_private_probes": False,
        "api_token": "a-long-random-token",
        "managed_targets": {"cloudflare": {"zones": ["zone-1"]}},
        "stability_seconds": 900,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_development_keeps_the_lab_and_private_probes() -> None:
    settings = Settings(_env_file=None)
    assert settings.environment == "development"
    assert settings.lab_enabled is True
    assert settings.allow_private_probes is True


def test_production_accepts_a_complete_configuration() -> None:
    settings = _production()
    assert settings.environment == "production"
    assert settings.lab_enabled is False


def test_production_accepts_oidc_instead_of_a_static_token() -> None:
    settings = _production(api_token=None, oidc_issuer="https://id.example.com")
    assert settings.oidc_issuer == "https://id.example.com"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"lab_enabled": True}, "NIGHTJAR_LAB_ENABLED"),
        ({"allow_private_probes": True}, "NIGHTJAR_ALLOW_PRIVATE_PROBES"),
        ({"api_token": None}, "NIGHTJAR_API_TOKEN"),
        ({"managed_targets": {}}, "NIGHTJAR_MANAGED_TARGETS"),
        ({"stability_seconds": 10}, "NIGHTJAR_STABILITY_SECONDS"),
    ],
)
def test_production_refuses_each_unsafe_setting(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _production(**overrides)
    assert message in str(excinfo.value)


def test_production_reports_every_problem_at_once() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _production(lab_enabled=True, allow_private_probes=True, managed_targets={})
    text = str(excinfo.value)
    assert "NIGHTJAR_LAB_ENABLED" in text
    assert "NIGHTJAR_ALLOW_PRIVATE_PROBES" in text
    assert "NIGHTJAR_MANAGED_TARGETS" in text


def test_managed_targets_parse_from_json_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "NIGHTJAR_MANAGED_TARGETS",
        '{"github": {"repos": ["acme/site"], "orgs": ["acme"]}}',
    )
    settings = Settings(_env_file=None)
    assert settings.managed_targets["github"]["repos"] == ["acme/site"]


def test_google_requires_a_managed_mailbox_in_production() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _production(google_service_account_file="/etc/nightjar/google.json")
    assert "google.mailboxes" in str(excinfo.value)


def test_google_with_a_managed_mailbox_is_accepted() -> None:
    settings = _production(
        google_service_account_file="/etc/nightjar/google.json",
        managed_targets={"google": {"mailboxes": ["owner@acme.example"]}},
    )
    assert settings.google_service_account_file == "/etc/nightjar/google.json"
