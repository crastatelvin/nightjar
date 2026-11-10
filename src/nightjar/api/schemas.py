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


class ChainStatus(BaseModel):
    evidence_valid: bool
    timeline_valid: bool
    evidence_checked: int
    timeline_checked: int
    broken_at: str | None = None


class CaseFileResponse(BaseModel):
    incident: Incident
    evidence: list[EvidenceItem]
    timeline: list[TimelineEvent]
    plan: ActionPlan | None = None
    actions: list[ActionSpec]
    action_states: dict[str, ActionStatus]
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


__all__ = [
    "ApprovalDecision",
    "CaseFileResponse",
    "ChainStatus",
    "IncidentCreate",
    "IncidentList",
    "IncidentListItem",
    "MetaInfo",
    "NoteCreate",
    "TimelineList",
]
