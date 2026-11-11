import type {
  CaseFile,
  Incident,
  IncidentList,
  IncidentStatus,
  LabAudit,
  MetaInfo,
} from "./types";

export const DEFAULT_API_BASE =
  process.env.NEXT_PUBLIC_NIGHTJAR_API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function getApiBase(): string {
  if (typeof window === "undefined") return DEFAULT_API_BASE;
  return window.localStorage.getItem("nightjar.apiBase") || DEFAULT_API_BASE;
}

export function getToken(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem("nightjar.token") || "";
}

export function getActor(): string {
  if (typeof window === "undefined") return "operator";
  return window.localStorage.getItem("nightjar.actor") || "operator";
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  options: { auth?: boolean } = {},
): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers);
  headers.set("accept", "application/json");
  if (init.body && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  if (options.auth !== false && token) {
    headers.set("authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // keep the status line
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

export interface CreateIncidentInput {
  title: string;
  description: string;
  incident_class: string;
  severity: string;
  source: string;
  metadata: Record<string, unknown>;
}

export const api = {
  meta: () => request<MetaInfo>("/v1/meta"),
  listIncidents: (limit = 100, status?: IncidentStatus) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (status) params.set("status", status);
    return request<IncidentList>(`/v1/incidents?${params.toString()}`);
  },
  getIncident: (id: string) => request<CaseFile>(`/v1/incidents/${id}`),
  createIncident: (input: CreateIncidentInput) =>
    request<Incident>("/v1/incidents?wait=false", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  decideApproval: (
    incidentId: string,
    approvalId: string,
    approved: boolean,
    comment: string,
  ) =>
    request<CaseFile>(`/v1/incidents/${incidentId}/approvals/${approvalId}`, {
      method: "POST",
      headers: { "x-nightjar-actor": getActor() },
      body: JSON.stringify({ approved, comment }),
    }),
  addNote: (incidentId: string, body: string) =>
    request<CaseFile>(`/v1/incidents/${incidentId}/notes`, {
      method: "POST",
      headers: { "x-nightjar-actor": getActor() },
      body: JSON.stringify({ body }),
    }),
  retry: (incidentId: string) =>
    request<Incident>(`/v1/incidents/${incidentId}/retry`, { method: "POST" }),
  verifyNow: (incidentId: string, phase: "t0" | "stability" = "t0") =>
    request<CaseFile>(`/v1/incidents/${incidentId}/verify?phase=${phase}`, {
      method: "POST",
    }),
  health: () => request<{ status: string }>("/healthz", {}, { auth: false }),
};

/** The lab lives on its own port, so browser calls go through the Next route. */
export async function labAudit(): Promise<LabAudit> {
  const response = await fetch("/api/demo/audit", { cache: "no-store" });
  if (!response.ok) {
    throw new ApiError(`lab audit failed (${response.status})`, response.status);
  }
  return (await response.json()) as LabAudit;
}

export async function resetLab(): Promise<void> {
  const response = await fetch("/api/demo/reset", { method: "POST" });
  if (!response.ok) {
    throw new ApiError(`lab reset failed (${response.status})`, response.status);
  }
}

export interface RunScenarioInput {
  scenario: string;
  variant: number;
  injection: boolean;
  relapse?: boolean;
  severity?: string;
}

export interface RunScenarioResult {
  incident: Incident;
  audit: LabAudit;
  scenario: string;
  variant: number;
  injection: boolean;
  relapse: boolean;
}

export async function runScenario(input: RunScenarioInput): Promise<RunScenarioResult> {
  const response = await fetch("/api/demo/run", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { error?: string };
      if (body.error) detail = body.error;
    } catch {
      // keep the status line
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as RunScenarioResult;
}
