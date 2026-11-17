from __future__ import annotations

from enum import StrEnum


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IncidentClass(StrEnum):
    SITE_OUTAGE = "site_outage"
    CREDENTIAL_EXPOSURE = "credential_exposure"
    PHISHING = "phishing"
    ACCOUNT_TAKEOVER = "account_takeover"
    UNKNOWN = "unknown"


class IncidentStatus(StrEnum):
    INTAKE = "intake"
    INVESTIGATING = "investigating"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_MANUAL = "awaiting_manual"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    REOPENED = "reopened"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IncidentSource(StrEnum):
    WEBHOOK = "webhook"
    EMAIL = "email"
    UI = "ui"
    API = "api"
    SCHEDULE = "schedule"


class RiskClass(StrEnum):
    R0 = "r0"  # read-only
    R1 = "r1"  # reversible, no customer impact
    R2 = "r2"  # reversible, customer impact possible
    R3 = "r3"  # destructive, financial or irreversible


class Decision(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class EvidenceKind(StrEnum):
    ALERT = "alert"
    EMAIL = "email"
    LOG = "log"
    METRIC = "metric"
    HTTP_RESPONSE = "http_response"
    DNS = "dns"
    TLS = "tls"
    CONFIG = "config"
    IDENTITY_EVENT = "identity_event"
    SECRET_SCAN = "secret_scan"  # noqa: S105 - evidence kind, not a credential
    DEPLOYMENT = "deployment"
    DOCUMENT = "document"
    MODEL_NOTE = "model_note"
    VERIFICATION = "verification"
    HUMAN_NOTE = "human_note"


class EventType(StrEnum):
    INCIDENT_CREATED = "incident.created"
    STATUS_CHANGED = "incident.status_changed"
    EVIDENCE_COLLECTED = "evidence.collected"
    TRIAGE_COMPLETED = "triage.completed"
    FINDING_RECORDED = "finding.recorded"
    CRITIQUE_COMPLETED = "critique.completed"
    PLAN_PROPOSED = "plan.proposed"
    POLICY_DECIDED = "policy.decided"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    ACTION_STARTED = "action.started"
    ACTION_COMPLETED = "action.completed"
    ACTION_FAILED = "action.failed"
    ACTION_REPLAYED = "action.replayed"
    MANUAL_STEP_REQUIRED = "manual_step.required"
    MANUAL_STEP_ATTESTED = "manual_step.attested"
    VERIFICATION_COMPLETED = "verification.completed"
    INCIDENT_RESOLVED = "incident.resolved"
    INCIDENT_REOPENED = "incident.reopened"
    NOTE = "note"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    DENIED = "denied"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_MANUAL = "awaiting_manual"
    ATTESTED = "attested"
    APPROVED = "approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    CANCELLED = "cancelled"


class PredicateKind(StrEnum):
    HTTP = "http"
    JSON = "json"
    DNS = "dns"
    TLS = "tls"
    CREDENTIAL = "credential"
    CONNECTOR_CHECK = "connector_check"


class ActionType(StrEnum):
    PROBE = "probe"
    PURGE_CACHE = "purge_cache"
    ENABLE_UNDER_ATTACK = "enable_under_attack"
    REVOKE_CREDENTIAL = "revoke_credential"
    QUARANTINE_MESSAGE = "quarantine_message"
    BLOCK_SENDER = "block_sender"
    ROLLBACK_DEPLOYMENT = "rollback_deployment"
    RESTART_SERVICE = "restart_service"
    OPEN_ISSUE = "open_issue"
    NOTIFY = "notify"


class CritiqueVerdict(StrEnum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"


class ClaimStatus(StrEnum):
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
