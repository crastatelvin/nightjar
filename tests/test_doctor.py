from __future__ import annotations

import json

import pytest

from nightjar.cli.doctor import _render, main
from nightjar.connectors.base import CapabilityReport
from nightjar.connectors.lab import LabConnector
from nightjar.core.capabilities import assess


@pytest.fixture
def lab_settings(monkeypatch: pytest.MonkeyPatch, lab_url: str) -> None:
    monkeypatch.setenv("NIGHTJAR_LAB_ENABLED", "true")
    monkeypatch.setenv("NIGHTJAR_LAB_BASE_URL", lab_url)
    monkeypatch.setenv("NIGHTJAR_ENVIRONMENT", "test")
    monkeypatch.delenv("NIGHTJAR_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("NIGHTJAR_CLOUDFLARE_TOKEN", raising=False)
    monkeypatch.delenv("NIGHTJAR_NOTIFY_WEBHOOK_URL", raising=False)


def test_doctor_exits_zero_when_every_v1_class_is_covered(
    lab_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "OK: every v1 incident class" in out
    assert "purge_cache" in out
    assert "quarantine_message" in out


def test_doctor_json_is_machine_readable(
    lab_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["environment"] == "test"
    providers = {item["provider"]: item for item in payload["providers"]}
    assert providers["lab"]["verdict"] == "ok"
    assert providers["http"]["verdict"] == "ok"


def test_doctor_narrows_to_one_incident_class(
    lab_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["doctor", "--class", "credential_exposure", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["missing"] == []


def test_doctor_fails_when_the_lab_is_off_and_no_provider_is_configured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NIGHTJAR_LAB_ENABLED", "false")
    monkeypatch.setenv("NIGHTJAR_ENVIRONMENT", "production")
    monkeypatch.setenv("NIGHTJAR_API_TOKEN", "a-long-random-token")
    monkeypatch.setenv("NIGHTJAR_MANAGED_TARGETS", '{"cloudflare": {"zones": ["z"]}}')
    monkeypatch.setenv("NIGHTJAR_ALLOW_PRIVATE_PROBES", "false")
    monkeypatch.setenv("NIGHTJAR_STABILITY_SECONDS", "900")
    monkeypatch.delenv("NIGHTJAR_CLOUDFLARE_TOKEN", raising=False)

    assert main(["doctor", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "quarantine_message" in payload["missing"]
    assert payload["providers"] == [
        {
            "checked_at": payload["providers"][0]["checked_at"],
            "detail": "public targets only",
            "identity": "read-only evidence prober",
            "latency_ms": None,
            "missing_scopes": [],
            "provider": "http",
            "verdict": "ok",
        }
    ]


def test_the_human_report_lists_missing_actions() -> None:
    report = assess(
        [LabConnector("http://127.0.0.1:8100")],
        [CapabilityReport("lab", "error", detail="lab returned 500")],
        environment="test",
    )
    rendered = _render(report)
    assert "NOT READY" in rendered
    assert "lab: lab returned 500" in rendered
    assert "missing" in rendered


def test_doctor_requires_a_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2
