import type { IncidentClass, IncidentStatus, Severity } from "./types";

export function cn(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  const diff = Date.now() - date.getTime();
  const future = diff < 0;
  const seconds = Math.abs(diff) / 1000;
  if (seconds < 45) return future ? "in a moment" : "just now";
  const minutes = seconds / 60;
  if (minutes < 60) return future ? `in ${Math.round(minutes)}m` : `${Math.round(minutes)}m ago`;
  const hours = minutes / 60;
  if (hours < 24) return future ? `in ${Math.round(hours)}h` : `${Math.round(hours)}h ago`;
  const days = hours / 24;
  if (days < 30) return future ? `in ${Math.round(days)}d` : `${Math.round(days)}d ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function clockTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function durationBetween(start: string, end?: string | null): string {
  const from = new Date(start).getTime();
  const to = end ? new Date(end).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((to - from) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

export function shortHash(value: string | undefined, size = 7): string {
  if (!value) return "—";
  return value.slice(0, size);
}

export function titleCase(value: string): string {
  return value
    .replace(/[._-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function secondsLeft(value: string | null | undefined): number {
  if (!value) return 0;
  return Math.max(0, Math.floor((new Date(value).getTime() - Date.now()) / 1000));
}

export function formatCountdown(seconds: number): string {
  if (seconds <= 0) return "expired";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${rest.toString().padStart(2, "0")}`;
}

export const STATUS_LABEL: Record<IncidentStatus, string> = {
  intake: "Intake",
  investigating: "Investigating",
  awaiting_approval: "Awaiting decision",
  awaiting_manual: "Awaiting manual step",
  executing: "Executing",
  verifying: "Verifying",
  resolved: "Resolved",
  reopened: "Reopened",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const STATUS_TONE: Record<
  IncidentStatus,
  "live" | "wait" | "good" | "bad" | "muted"
> = {
  intake: "live",
  investigating: "live",
  awaiting_approval: "wait",
  awaiting_manual: "wait",
  executing: "live",
  verifying: "live",
  resolved: "good",
  reopened: "bad",
  failed: "bad",
  cancelled: "muted",
};

export const SEVERITY_TONE: Record<Severity, string> = {
  critical: "text-[var(--color-danger)]",
  high: "text-[var(--color-amber)]",
  medium: "text-[var(--color-sky)]",
  low: "text-[var(--color-muted)]",
};

export const CLASS_ICON: Record<IncidentClass, string> = {
  site_outage: "outage",
  credential_exposure: "key",
  phishing: "mail",
  account_takeover: "identity",
  unknown: "pulse",
};

export function isActive(status: IncidentStatus): boolean {
  return [
    "intake",
    "investigating",
    "awaiting_approval",
    "awaiting_manual",
    "executing",
    "verifying",
  ].includes(status);
}

export function classLabel(value: IncidentClass): string {
  if (value === "site_outage") return "Site outage";
  if (value === "credential_exposure") return "Credential exposure";
  if (value === "account_takeover") return "Account takeover";
  return titleCase(value);
}
