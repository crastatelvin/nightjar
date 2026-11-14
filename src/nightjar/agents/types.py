from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field

from nightjar.core.capabilities import ActionCapability
from nightjar.core.enums import IncidentClass, Severity
from nightjar.core.models import (
    ActionPlan,
    Claim,
    Critique,
    EvidenceItem,
    Finding,
    Incident,
    TriageResult,
)


class Environment(BaseModel):
    tenant_name: str = "Local Nightjar"
    site_url: str = "http://127.0.0.1:8100/site/"
    checkout_url: str = "http://127.0.0.1:8100/site/checkout"
    state_url: str = "http://127.0.0.1:8100/lab/state"


class TriageInput(BaseModel):
    incident: Incident
    environment: Environment


class ProbeRequest(BaseModel):
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class InvestigationInput(BaseModel):
    incident: Incident
    triage: TriageResult
    evidence: list[EvidenceItem] = Field(default_factory=list)
    environment: Environment
    round: int = 1
    available_tools: list[str] = Field(default_factory=list)


class InvestigationOutput(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    probes: list[ProbeRequest] = Field(default_factory=list)
    note: str = ""


class CritiqueInput(BaseModel):
    incident: Incident
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class PlanInput(BaseModel):
    incident: Incident
    triage: TriageResult
    findings: list[Finding] = Field(default_factory=list)
    critique: Critique | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    environment: Environment
    # Only what this deployment can really do. A planner that is not told this
    # will propose a fix no connector implements, and the incident fails later
    # instead of at plan time.
    capabilities: list[ActionCapability] = Field(default_factory=list)


@dataclass
class AgentOutcome[T]:
    output: T
    provider: str
    model: str
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class AgentRuntime(Protocol):
    async def triage(self, payload: TriageInput) -> AgentOutcome[TriageResult]: ...

    async def investigate(
        self, payload: InvestigationInput
    ) -> AgentOutcome[InvestigationOutput]: ...

    async def critique(self, payload: CritiqueInput) -> AgentOutcome[Critique]: ...

    async def plan(self, payload: PlanInput) -> AgentOutcome[ActionPlan]: ...


def evidence_ids(evidence: list[EvidenceItem]) -> dict[str, EvidenceItem]:
    return {item.id: item for item in evidence}


def claim(text: str, confidence: float, ids: list[str]) -> Claim:
    return Claim(text=text, confidence=confidence, evidence_ids=ids)


def severity_from(value: str) -> Severity:
    try:
        return Severity(value)
    except ValueError:
        return Severity.MEDIUM


def classify_text(title: str, description: str) -> tuple[IncidentClass, str]:
    blob = f"{title} {description}".lower()
    if any(word in blob for word in ("phish", "suspicious email", "spoof", "reported email")):
        return IncidentClass.PHISHING, "Phishing report"
    if any(
        word in blob
        for word in ("api key", "credential", "secret", "token", "leaked", "exposed key")
    ):
        return IncidentClass.CREDENTIAL_EXPOSURE, "Exposed credential"
    if any(
        word in blob
        for word in ("down", "outage", "500", "503", "checkout", "website", "storefront")
    ):
        return IncidentClass.SITE_OUTAGE, "Site outage"
    return IncidentClass.UNKNOWN, "Unclassified incident"


@dataclass
class InvestigationBundle:
    triage: TriageResult
    findings: list[Finding]
    critique: Critique
    evidence: list[EvidenceItem] = field(default_factory=list)
    cost_usd: float = 0.0


__all__ = [
    "AgentOutcome",
    "AgentRuntime",
    "CritiqueInput",
    "Environment",
    "InvestigationBundle",
    "InvestigationInput",
    "InvestigationOutput",
    "PlanInput",
    "ProbeRequest",
    "TriageInput",
    "claim",
    "classify_text",
    "evidence_ids",
    "severity_from",
]
