from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nightjar.core.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    ClaimStatus,
    CritiqueVerdict,
    Decision,
    EventType,
    EvidenceKind,
    IncidentClass,
    IncidentSource,
    IncidentStatus,
    PredicateKind,
    RiskClass,
    Severity,
)
from nightjar.core.ids import new_id, utcnow


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Incident(StrictModel):
    id: str = Field(default_factory=new_id)
    tenant_id: str
    title: str
    description: str = ""
    incident_class: IncidentClass = IncidentClass.UNKNOWN
    severity: Severity = Severity.MEDIUM
    status: IncidentStatus = IncidentStatus.INTAKE
    source: IncidentSource = IncidentSource.API
    dedupe_key: str | None = None
    reported_by: str = ""
    summary: str | None = None
    root_cause: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    resolved_at: datetime | None = None


class EvidenceItem(StrictModel):
    id: str = Field(default_factory=new_id)
    incident_id: str
    kind: EvidenceKind
    source: str
    summary: str
    content: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=utcnow)
    collected_by: str = "system"
    content_hash: str = ""
    prev_hash: str = ""
    hash: str = ""


class Claim(StrictModel):
    id: str = Field(default_factory=new_id)
    text: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    status: ClaimStatus = ClaimStatus.UNVERIFIED

    @field_validator("evidence_ids")
    @classmethod
    def dedupe_evidence(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in value:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result


class Finding(StrictModel):
    id: str = Field(default_factory=new_id)
    title: str
    detail: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    claims: list[Claim] = Field(default_factory=list)
    author: str = "investigator"
    created_at: datetime = Field(default_factory=utcnow)


class TriageResult(StrictModel):
    incident_class: IncidentClass
    severity: Severity
    title: str
    summary: str
    affected_assets: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    suggested_checks: list[str] = Field(default_factory=list)


class ClaimVerdict(StrictModel):
    claim_id: str
    verdict: CritiqueVerdict
    reason: str


class Critique(StrictModel):
    verdicts: list[ClaimVerdict] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    overall: CritiqueVerdict = CritiqueVerdict.PARTIAL
    confidence_delta: float = Field(default=0.0, ge=-1.0, le=1.0)
    notes: str = ""
    reviewer: str = "critic"


class VerificationPredicate(StrictModel):
    id: str = Field(default_factory=new_id)
    kind: PredicateKind
    description: str
    params: dict[str, Any] = Field(default_factory=dict)
    required: bool = True


class ActionSpec(StrictModel):
    id: str = Field(default_factory=new_id)
    action_type: ActionType
    target: str
    params: dict[str, Any] = Field(default_factory=dict)
    risk: RiskClass = RiskClass.R1
    description: str = ""
    rollback: str | None = None
    idempotency_key: str | None = None
    predicate_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ActionPlan(StrictModel):
    id: str = Field(default_factory=new_id)
    summary: str
    root_cause: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    actions: list[ActionSpec] = Field(default_factory=list)
    predicates: list[VerificationPredicate] = Field(default_factory=list)
    blast_radius: list[str] = Field(default_factory=list)
    rollback_plan: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class PolicyResult(StrictModel):
    decision: Decision
    risk: RiskClass
    reasons: list[str] = Field(default_factory=list)
    matched_rules: list[str] = Field(default_factory=list)
    required_approvals: int = 0
    engine: str = "local"


class ApprovalRequest(StrictModel):
    id: str = Field(default_factory=new_id)
    incident_id: str
    action_id: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    approved_by: str | None = None
    approved_at: datetime | None = None
    comment: str = ""
    token_hash: str = ""
    requested_by: str = ""


class VerificationResult(StrictModel):
    id: str = Field(default_factory=new_id)
    incident_id: str
    action_id: str | None = None
    predicate_id: str
    kind: PredicateKind
    passed: bool
    observed: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=utcnow)
    error: str | None = None
    phase: str = "t0"
    required: bool = True


class TimelineEvent(StrictModel):
    id: str = Field(default_factory=new_id)
    incident_id: str
    seq: int = 0
    event_type: EventType
    actor: str = "system"
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    prev_hash: str = ""
    hash: str = ""


class ChainVerification(StrictModel):
    valid: bool
    checked: int
    broken_at: str | None = None
    reason: str | None = None


class ActionExecution(StrictModel):
    id: str = Field(default_factory=new_id)
    incident_id: str
    action_id: str
    status: ActionStatus = ActionStatus.PROPOSED
    before_state: dict[str, Any] = Field(default_factory=dict)
    after_state: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AgentRun(StrictModel):
    id: str = Field(default_factory=new_id)
    incident_id: str
    role: str
    provider: str
    model: str
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
