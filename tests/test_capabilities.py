from __future__ import annotations

import pytest

from nightjar.connectors.base import Capability, CapabilityReport
from nightjar.connectors.http import HttpConnector
from nightjar.connectors.lab import LabConnector
from nightjar.core.capabilities import (
    INCIDENT_REQUIREMENTS,
    V1_CLASSES,
    assess,
    collect_capabilities,
    required_actions,
)
from nightjar.core.enums import ActionType, IncidentClass


def lab() -> LabConnector:
    return LabConnector("http://127.0.0.1:8100")


def test_every_v1_class_declares_at_least_one_action() -> None:
    assert set(INCIDENT_REQUIREMENTS) == set(V1_CLASSES)
    assert all(INCIDENT_REQUIREMENTS[item] for item in V1_CLASSES)


def test_required_actions_are_deduplicated_in_order() -> None:
    actions = required_actions()
    assert len(actions) == len(set(actions))
    assert actions[0] is ActionType.PURGE_CACHE
    assert ActionType.REVOKE_CREDENTIAL in actions


def test_a_single_class_narrows_the_requirement() -> None:
    assert required_actions((IncidentClass.CREDENTIAL_EXPOSURE,)) == [ActionType.REVOKE_CREDENTIAL]


def test_capabilities_are_grouped_by_action() -> None:
    index = collect_capabilities([lab(), HttpConnector()])
    assert ActionType.PURGE_CACHE in index
    assert all(item.provider == "lab" for item in index[ActionType.PURGE_CACHE])


def test_a_connector_cannot_advertise_another_providers_action() -> None:
    class Liar(LabConnector):
        def capabilities(self) -> tuple[Capability, ...]:
            return (Capability(ActionType.PURGE_CACHE, provider="cloudflare"),)

    with pytest.raises(ValueError, match="advertised a capability for cloudflare"):
        collect_capabilities([Liar("http://127.0.0.1:8100")])


def test_a_connector_cannot_advertise_an_action_it_does_not_implement() -> None:
    class Liar(LabConnector):
        action_types = frozenset({ActionType.PURGE_CACHE})

        def capabilities(self) -> tuple[Capability, ...]:
            return (Capability(ActionType.RESTART_SERVICE, provider=self.name),)

    with pytest.raises(ValueError, match="does not implement"):
        collect_capabilities([Liar("http://127.0.0.1:8100")])


def test_the_lab_satisfies_every_v1_requirement() -> None:
    connectors = [lab(), HttpConnector()]
    report = assess(
        connectors,
        [CapabilityReport("lab", "ok"), CapabilityReport("http", "ok")],
        environment="test",
    )
    assert report.ok is True
    assert report.missing == []
    assert {action.verdict for action in report.actions} == {"ok"}


def test_a_read_only_prober_satisfies_nothing() -> None:
    report = assess(
        [HttpConnector()],
        [CapabilityReport("http", "ok")],
        environment="production",
    )
    assert report.ok is False
    assert set(report.missing) == set(required_actions())
    assert any("no healthy provider" in problem for problem in report.problems)


def test_an_unprobed_report_claims_configuration_not_health() -> None:
    report = assess([lab()], [], environment="test", probed=False)
    assert report.ok is True
    assert {action.verdict for action in report.actions} == {"configured"}
    assert report.providers == []


def test_a_degraded_provider_does_not_satisfy_a_requirement() -> None:
    report = assess(
        [lab()],
        [CapabilityReport("lab", "degraded", detail="mailbox access missing")],
        environment="test",
    )
    assert report.ok is False
    assert ActionType.QUARANTINE_MESSAGE in report.missing
    assert any("degraded" in problem for problem in report.problems)


def test_an_erroring_provider_names_the_problem() -> None:
    report = assess(
        [lab()],
        [CapabilityReport("lab", "error", detail="lab returned 500")],
        environment="test",
    )
    assert report.ok is False
    assert "lab: lab returned 500" in report.problems


def test_the_report_is_json_safe() -> None:
    report = assess(
        [lab()],
        [CapabilityReport("lab", "ok", identity="cornershop")],
        environment="test",
    )
    payload = report.as_dict()
    assert payload["ok"] is True
    assert payload["providers"][0]["provider"] == "lab"  # type: ignore[index]
    assert payload["actions"][0]["target_kinds"] is not None  # type: ignore[index]
