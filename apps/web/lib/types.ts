export type IncidentStatus =
  | "intake"
  | "investigating"
  | "awaiting_approval"
  | "executing"
  | "verifying"
  | "resolved"
  | "reopened"
  | "failed"
  | "cancelled";

export type ClaimStatus =
  | "unverified"
  | "supported"
  | "partial"
  | "unsupported"
  | "contradicted";

export type Severity = "critical" | "high" | "medium" | "low";
export type IncidentClass =
  | "site_outage"
  | "credential_exposure"
  | "phishing"
  | "account_takeover"
  | "unknown";
export type RiskClass = "r0" | "r1" | "r2" | "r3";
export type ActionStatus =
  | "proposed"
  | "denied"
  | "awaiting_approval"
  | "approved"
  | "executing"
  | "executed"
  | "failed"
  | "rolled_back"
  | "cancelled";
export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired";

export interface IncidentSummary {
  id: string;
  title: string;
  incident_class: IncidentClass;
  severity: Severity;
  status: IncidentStatus;
  summary: string | null;
  confidence: number | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
}

export interface IncidentList {
  items: IncidentSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface Incident extends IncidentSummary {
  tenant_id: string;
  description: string;
  source: string;
  dedupe_key: string | null;
  root_cause: string | null;
  metadata: Record<string, unknown>;
}

export interface EvidenceItem {
  id: string;
  incident_id: string;
  kind: string;
  source: string;
  summary: string;
  content: Record<string, unknown>;
  observed_at: string;
  collected_by: string;
  content_hash: string;
  prev_hash: string;
  hash: string;
}

export interface TimelineEvent {
  id: string;
  incident_id: string;
  seq: number;
  event_type: string;
  actor: string;
  payload: Record<string, unknown>;
  created_at: string;
  prev_hash: string;
  hash: string;
}

export interface VerificationPredicate {
  id: string;
  kind: string;
  description: string;
  params: Record<string, unknown>;
  required: boolean;
}

export interface ActionSpec {
  id: string;
  action_type: string;
  target: string;
  params: Record<string, unknown>;
  risk: RiskClass;
  description: string;
  rollback: string | null;
  idempotency_key: string | null;
  predicate_ids: string[];
  evidence_ids: string[];
}

export interface ActionPlan {
  id: string;
  summary: string;
  root_cause: string;
  confidence: number;
  actions: ActionSpec[];
  predicates: VerificationPredicate[];
  blast_radius: string[];
  rollback_plan: string;
  created_at: string;
}

export interface ApprovalRequest {
  id: string;
  incident_id: string;
  action_id: string;
  status: ApprovalStatus;
  requested_at: string;
  expires_at: string;
  approved_by: string | null;
  approved_at: string | null;
  comment: string;
  token_hash: string;
}

export interface VerificationResult {
  id: string;
  incident_id: string;
  action_id: string | null;
  predicate_id: string;
  kind: string;
  passed: boolean;
  observed: Record<string, unknown>;
  checked_at: string;
  error: string | null;
  phase: string;
  required: boolean;
}

export interface ActionExecution {
  id: string;
  incident_id: string;
  action_id: string;
  status: ActionStatus;
  before_state: Record<string, unknown>;
  after_state: Record<string, unknown>;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface AgentRun {
  id: string;
  incident_id: string;
  role: string;
  provider: string;
  model: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  output: Record<string, unknown>;
  error: string | null;
  created_at: string;
}

export interface Claim {
  id: string;
  text: string;
  confidence: number;
  evidence_ids: string[];
  status: ClaimStatus;
}

export interface Finding {
  id: string;
  title: string;
  detail: string;
  confidence: number;
  claims: Claim[];
  author: string;
  created_at: string;
}

export interface ChainStatus {
  evidence_valid: boolean;
  timeline_valid: boolean;
  evidence_checked: number;
  timeline_checked: number;
  broken_at: string | null;
}

export interface CaseFile {
  incident: Incident;
  evidence: EvidenceItem[];
  timeline: TimelineEvent[];
  plan: ActionPlan | null;
  actions: ActionSpec[];
  action_states: Record<string, ActionStatus>;
  approvals: ApprovalRequest[];
  verifications: VerificationResult[];
  executions: ActionExecution[];
  agent_runs: AgentRun[];
  notes: { id?: string; body?: string; author?: string; created_at?: string }[];
  findings: Finding[];
  chain: ChainStatus;
}

export interface MetaInfo {
  name: string;
  version: string;
  provider: string;
  model: string;
  connectors: string[];
  tools: string[];
  action_catalog: Record<string, string>;
}

export interface LabAudit {
  healthy: boolean;
  checks: Record<string, boolean>;
  site_mode: string;
}
