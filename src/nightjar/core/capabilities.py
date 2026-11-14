from __future__ import annotations

from dataclasses import dataclass, field

from nightjar.connectors.base import Capability, CapabilityReport, Connector
from nightjar.core.enums import ActionType, IncidentClass

# What the v1 incident classes need in order to be closed without a human
# improvising. A class is only "supported" when every action here has a healthy
# provider; otherwise the UI and `nightjarctl doctor` say exactly what is missing.
INCIDENT_REQUIREMENTS: dict[IncidentClass, tuple[ActionType, ...]] = {
    IncidentClass.SITE_OUTAGE: (
        ActionType.PURGE_CACHE,
        ActionType.ROLLBACK_DEPLOYMENT,
    ),
    IncidentClass.CREDENTIAL_EXPOSURE: (ActionType.REVOKE_CREDENTIAL,),
    IncidentClass.PHISHING: (
        ActionType.QUARANTINE_MESSAGE,
        ActionType.BLOCK_SENDER,
    ),
}

V1_CLASSES: tuple[IncidentClass, ...] = (
    IncidentClass.SITE_OUTAGE,
    IncidentClass.CREDENTIAL_EXPOSURE,
    IncidentClass.PHISHING,
)


@dataclass(frozen=True)
class ActionCapability:
    """One action, one provider, and whether that provider is healthy right now."""

    action_type: ActionType
    provider: str
    automated: bool
    target_kinds: tuple[str, ...]
    notes: str
    verdict: str

    def as_dict(self) -> dict[str, object]:
        return {
            "action_type": self.action_type.value,
            "provider": self.provider,
            "automated": self.automated,
            "target_kinds": list(self.target_kinds),
            "notes": self.notes,
            "verdict": self.verdict,
        }


@dataclass
class DoctorReport:
    providers: list[CapabilityReport] = field(default_factory=list)
    actions: list[ActionCapability] = field(default_factory=list)
    missing: list[ActionType] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    environment: str = "development"

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "environment": self.environment,
            "providers": [report.as_dict() for report in self.providers],
            "actions": [action.as_dict() for action in self.actions],
            "missing": [action.value for action in self.missing],
            "problems": list(self.problems),
        }


def required_actions(classes: tuple[IncidentClass, ...] = V1_CLASSES) -> list[ActionType]:
    required: list[ActionType] = []
    for incident_class in classes:
        for action in INCIDENT_REQUIREMENTS.get(incident_class, ()):
            if action not in required:
                required.append(action)
    return required


def collect_capabilities(connectors: list[Connector]) -> dict[ActionType, list[Capability]]:
    """Every action the configured connectors claim, grouped by action type."""
    index: dict[ActionType, list[Capability]] = {}
    for connector in connectors:
        for capability in connector.capabilities():
            if capability.provider != connector.name:
                raise ValueError(
                    f"connector {connector.name} advertised a capability for {capability.provider}"
                )
            if capability.action_type not in connector.action_types:
                raise ValueError(
                    f"connector {connector.name} advertised {capability.action_type} "
                    "but does not implement it"
                )
            index.setdefault(capability.action_type, []).append(capability)
    return index


def assess(
    connectors: list[Connector],
    reports: list[CapabilityReport],
    *,
    environment: str = "development",
    classes: tuple[IncidentClass, ...] = V1_CLASSES,
    probed: bool = True,
) -> DoctorReport:
    """Turn provider reports into a verdict an operator can act on.

    A provider that is degraded still appears in the action table, but it does
    not satisfy a requirement. That keeps the table honest: the action exists,
    it just cannot be trusted to run today.

    With ``probed=False`` the report only describes what is configured, and
    every action is marked ``configured`` rather than ``ok``: no network was
    touched, so nothing is claimed about health.
    """
    healthy = {report.provider for report in reports if report.healthy}
    index = collect_capabilities(connectors)

    actions: list[ActionCapability] = []
    available: set[ActionType] = set()
    for action_type, capabilities in sorted(index.items(), key=lambda item: item[0].value):
        for capability in capabilities:
            if not probed:
                verdict = "configured"
            else:
                verdict = "ok" if capability.provider in healthy else "unavailable"
            if verdict in {"ok", "configured"}:
                available.add(action_type)
            actions.append(
                ActionCapability(
                    action_type=action_type,
                    provider=capability.provider,
                    automated=capability.automated,
                    target_kinds=capability.target_kinds,
                    notes=capability.notes,
                    verdict=verdict,
                )
            )

    required = required_actions(classes)
    missing = [action for action in required if action not in available]

    problems: list[str] = []
    if probed:
        for report in reports:
            if report.verdict == "error":
                problems.append(f"{report.provider}: {report.detail or 'failed its preflight'}")
            elif report.verdict == "degraded":
                problems.append(
                    f"{report.provider}: degraded — {report.detail or 'partial access'}"
                )
    for action in missing:
        problems.append(f"no healthy provider for {action.value}")

    return DoctorReport(
        providers=list(reports),
        actions=actions,
        missing=missing,
        problems=problems,
        environment=environment,
    )
