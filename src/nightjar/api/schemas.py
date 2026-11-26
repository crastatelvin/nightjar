from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from nightjar.core.enums import (
    ActionStatus,
    IncidentClass,
    IncidentSource,
    IncidentStatus,
    Severity,
)
from nightjar.core.models import (
    ActionExecution,
    ActionPlan,
    ActionSpec,
    AgentRun,
    ApprovalRequest,
    EvidenceItem,
    Finding,
    Incident,
    TimelineEvent,
    VerificationResult,
)


class IncidentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    description: str = ""
    incident_class: IncidentClass = IncidentClass.UNKNOWN
    severity: Severity = Severity.MEDIUM
    source: IncidentSource = IncidentSource.API
    metadata: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str | None = Field(default=None, max_length=200)


class IncidentListItem(BaseModel):
    id: str
    title: str
    incident_class: IncidentClass
    severity: Severity
    status: IncidentStatus
    summary: str | None = None
    confidence: float | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None


class IncidentList(BaseModel):
    items: list[IncidentListItem]
    total: int
    limit: int
    offset: int


class ApprovalDecision(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=2000)


class NoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class ManualStepAttest(BaseModel):
    note: str = Field(default="", max_length=2000)


class WebhookIntake(BaseModel):
    """The incident an alert source may file over a signed webhook."""

    title: str = Field(min_length=3, max_length=300)
    description: str = ""
    incident_class: IncidentClass = IncidentClass.UNKNOWN
    severity: Severity = Severity.MEDIUM
    metadata: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str | None = Field(default=None, max_length=200)


class IntakeAccepted(BaseModel):
    incident_id: str
    delivery_id: str
    deduplicated: bool


class ManualStepView(BaseModel):
    action_id: str
    action_type: str
    target: str
    description: str
    risk: str
    state: ActionStatus
    provider: str
    title: str
    instructions: list[str]
    dashboard_url: str = ""
    fingerprint: str = ""
    probe_url: str = ""
    auth: str = "bearer"


class ChainStatus(BaseModel):
    evidence_valid: bool
    timeline_valid: bool
    evidence_checked: int
    timeline_checked: int
    broken_at: str | None = None


class ActionView(ActionSpec):
    """A stored action plus whether a human has to perform it.

    The flag is derived from the bound connector's capability, not stored on the
    plan. An approver needs to know before signing off that the "revocation" is a
    dashboard step with a verification probe, not an API call Nightjar can make.
    """

    manual: bool = False


class CaseFileResponse(BaseModel):
    incident: Incident
    evidence: list[EvidenceItem]
    timeline: list[TimelineEvent]
    plan: ActionPlan | None = None
    actions: list[ActionView]
    action_states: dict[str, ActionStatus]
    manual_steps: list[ManualStepView] = Field(default_factory=list)
    approvals: list[ApprovalRequest]
    verifications: list[VerificationResult]
    executions: list[ActionExecution]
    agent_runs: list[AgentRun]
    notes: list[dict[str, Any]]
    findings: list[Finding]
    chain: ChainStatus


class TimelineList(BaseModel):
    events: list[TimelineEvent]


class MetaInfo(BaseModel):
    name: str
    version: str
    provider: str
    model: str
    connectors: list[str]
    tools: list[str]
    action_catalog: dict[str, str]


class ProviderHealth(BaseModel):
    provider: str
    verdict: str
    identity: str = ""
    detail: str = ""
    missing_scopes: list[str] = Field(default_factory=list)
    latency_ms: int | None = None


class ActionCapabilityView(BaseModel):
    action_type: str
    provider: str
    automated: bool
    target_kinds: list[str] = Field(default_factory=list)
    notes: str = ""
    proof: str = ""
    verdict: str


class CapabilitiesResponse(BaseModel):
    ok: bool
    environment: str
    probed: bool
    providers: list[ProviderHealth]
    actions: list[ActionCapabilityView]
    missing: list[str]
    problems: list[str]


__all__ = [
    "ActionCapabilityView",
    "ActionView",
    "ApprovalDecision",
    "CapabilitiesResponse",
    "CaseFileResponse",
    "ChainStatus",
    "IncidentCreate",
    "IncidentList",
    "IncidentListItem",
    "IntakeAccepted",
    "ManualStepAttest",
    "ManualStepView",
    "MetaInfo",
    "NoteCreate",
    "ProviderHealth",
    "TimelineList",
    "WebhookIntake",
]
