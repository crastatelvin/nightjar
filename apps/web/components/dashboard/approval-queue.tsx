"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { classLabel, cn, relativeTime } from "@/lib/format";
import type { CaseFile, IncidentSummary, IncidentStatus, Severity } from "@/lib/types";
import { useToast } from "@/components/toast";
import {
  IconAlert,
  IconArrow,
  IconCheck,
  IconClock,
  IconLink,
  IconLock,
  IconShield,
  IconX,
  IconZap,
} from "@/components/icons";
import { StatusBadge } from "@/components/status-badge";
import { Meter } from "@/components/data-viz";
import { secondsLeft, formatCountdown } from "@/lib/format";

export function ApprovalCard({
  caseFile,
  onChanged,
}: {
  caseFile: CaseFile;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const pending = caseFile.approvals.find((approval) => approval.status === "pending");
  const [remaining, setRemaining] = useState(() => secondsLeft(pending?.expires_at));

  useEffect(() => {
    const timer = setInterval(() => setRemaining(secondsLeft(pending?.expires_at)), 1000);
    return () => clearInterval(timer);
  }, [pending?.expires_at]);

  if (!pending) return null;
  const action = caseFile.plan?.actions.find((item) => item.id === pending.action_id);

  const decide = async (approved: boolean) => {
    setBusy(approved ? "approve" : "reject");
    try {
      await api.decideApproval(caseFile.incident.id, pending.id, approved, "");
      toast(
        approved
          ? "Approved. The executor will run and verify now."
          : "Rejected. Nothing will execute.",
        approved ? "good" : "bad",
      );
      onChanged();
    } catch (error) {
      toast(error instanceof Error ? error.message : "Decision failed", "bad");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="panel corner-cut overflow-hidden p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="status-dot wait text-[var(--color-amber)]" />
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-[var(--color-amber)]">
              Decision required
            </p>
          </div>
          <Link
            href={`/incidents/${caseFile.incident.id}`}
            className="mt-1.5 block truncate text-[13px] font-medium text-[var(--color-text)] underline-grow"
          >
            {caseFile.incident.title}
          </Link>
        </div>
        <span className="mono flex items-center gap-1 text-[10px] text-[var(--color-muted)]">
          <IconClock className="h-3 w-3" />
          {formatCountdown(remaining)}
        </span>
      </div>

      {action && (
        <div className="mt-3 rounded-lg border border-[var(--color-line-soft)] bg-white/[0.02] px-3 py-2.5">
          <div className="flex items-center gap-2">
            <span className="mono text-[10px] uppercase tracking-wide text-[var(--color-dim)]">
              {action.risk}
            </span>
            <span className="text-[12px] font-medium text-[var(--color-text)]">
              {action.description || action.action_type}
            </span>
          </div>
          <p className="mono mt-1 truncate text-[10px] text-[var(--color-dim)]">
            {action.target}
          </p>
        </div>
      )}

      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => decide(true)}
          className="group flex h-9 flex-1 items-center justify-center gap-1.5 rounded-lg border border-[rgba(200,255,114,0.3)] bg-[rgba(200,255,114,0.09)] text-[12px] font-semibold text-[var(--color-acid)] transition hover:bg-[rgba(200,255,114,0.15)] disabled:opacity-50"
        >
          {busy === "approve" ? (
            <span className="h-3 w-3 animate-spin-slow rounded-full border border-current border-t-transparent" />
          ) : (
            <IconCheck className="h-3.5 w-3.5" />
          )}
          Approve
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => decide(false)}
          className="flex h-9 items-center justify-center gap-1.5 rounded-lg border border-[var(--color-line)] px-3 text-[12px] text-[var(--color-muted)] transition hover:border-[rgba(255,111,97,0.35)] hover:text-[var(--color-danger)] disabled:opacity-50"
        >
          {busy === "reject" ? (
            <span className="h-3 w-3 animate-spin-slow rounded-full border border-current border-t-transparent" />
          ) : (
            <IconX className="h-3.5 w-3.5" />
          )}
          Reject
        </button>
      </div>
      <p className="mt-2.5 text-[10px] leading-relaxed text-[var(--color-dim)]">
        Execution requires your approval. Verification runs immediately after.
      </p>
    </div>
  );
}

const CLASS_TONE: Record<string, string> = {
  site_outage: "text-[var(--color-sky)]",
  credential_exposure: "text-[var(--color-amber)]",
  phishing: "text-[var(--color-violet)]",
  account_takeover: "text-[var(--color-rose)]",
  unknown: "text-[var(--color-muted)]",
};

export function IncidentRow({
  incident,
  index,
}: {
  incident: IncidentSummary;
  index: number;
}) {
  return (
    <Link
      href={`/incidents/${incident.id}`}
      className="group row-hover relative grid grid-cols-[auto_1fr_auto] items-center gap-3 border-b border-[var(--color-line-soft)] px-3 py-3.5 last:border-0 sm:grid-cols-[auto_1fr_120px_92px_auto] sm:gap-4 sm:px-4"
      style={{ animationDelay: `${index * 35}ms` }}
    >
      <span className="absolute left-0 top-1/2 h-0 w-[2px] -translate-y-1/2 rounded-full bg-[var(--color-acid)] opacity-0 transition-all duration-300 group-hover:h-7 group-hover:opacity-100" />
      <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--color-line)] bg-[var(--color-ink)]">
        <SeverityGlyph severity={incident.severity} />
      </div>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <p className="truncate text-[13px] font-medium text-[var(--color-text)] transition group-hover:text-white">
            {incident.title}
          </p>
          {incident.status === "reopened" && (
            <span className="mono rounded border border-[rgba(255,111,97,0.3)] px-1 text-[9px] uppercase text-[var(--color-danger)]">
              relapse
            </span>
          )}
        </div>
        <p className="mt-0.5 flex items-center gap-1.5 truncate text-[11px] text-[var(--color-dim)]">
          <span className={cn("font-medium", CLASS_TONE[incident.incident_class])}>
            {classLabel(incident.incident_class)}
          </span>
          <span className="text-[var(--color-line)]">·</span>
          <span className="mono">{incident.id.slice(0, 8)}</span>
          <span className="hidden text-[var(--color-line)] sm:inline">·</span>
          <span className="hidden sm:inline">{relativeTime(incident.created_at)}</span>
        </p>
      </div>
      <div className="hidden items-center gap-2 sm:flex">
        {incident.confidence !== null ? (
          <>
            <Meter
              value={incident.confidence}
              tone={incident.confidence >= 0.7 ? "acid" : "amber"}
              className="w-14"
            />
            <span className="mono text-[10px] text-[var(--color-dim)]">
              {Math.round(incident.confidence * 100)}%
            </span>
          </>
        ) : (
          <span className="text-[10px] text-[var(--color-dim)]">no confidence</span>
        )}
      </div>
      <div className="hidden justify-end sm:flex">
        <StatusBadge status={incident.status} size="sm" />
      </div>
      <div className="flex items-center gap-2">
        <span className="sm:hidden">
          <StatusBadge status={incident.status} size="sm" />
        </span>
        <IconArrow className="h-3.5 w-3.5 text-[var(--color-dim)] transition-all duration-200 group-hover:translate-x-0.5 group-hover:text-[var(--color-acid)]" />
      </div>
    </Link>
  );
}

function SeverityGlyph({ severity }: { severity: Severity }) {
  if (severity === "critical") return <IconAlert className="h-4 w-4 text-[var(--color-danger)]" />;
  if (severity === "high") return <IconZap className="h-4 w-4 text-[var(--color-amber)]" />;
  if (severity === "medium") return <IconShield className="h-4 w-4 text-[var(--color-sky)]" />;
  return <IconLink className="h-4 w-4 text-[var(--color-dim)]" />;
}

export function ApprovalQueue({
  cases,
  loading,
  onChanged,
}: {
  cases: CaseFile[];
  loading: boolean;
  onChanged: () => void;
}) {
  if (loading && cases.length === 0) {
    return <div className="shimmer h-40 rounded-2xl" />;
  }
  if (cases.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-[var(--color-line)] p-4">
        <div className="flex items-center gap-2 text-[var(--color-acid)]">
          <IconLock className="h-3.5 w-3.5" />
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em]">Queue clear</p>
        </div>
        <p className="mt-2 text-[12px] leading-relaxed text-[var(--color-muted)]">
          Nothing is waiting on a human. R2 actions will stop here when they need you.
        </p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {cases.map((caseFile) => (
        <ApprovalCard key={caseFile.incident.id} caseFile={caseFile} onChanged={onChanged} />
      ))}
    </div>
  );
}

export function NewIncidentButton({ className }: { className?: string }) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState("Checkout is failing across the storefront");
  const [description, setDescription] = useState(
    "Customers report errors at checkout. The health monitor started alerting two minutes ago.",
  );
  const [incidentClass, setIncidentClass] = useState("site_outage");
  const [severity, setSeverity] = useState("critical");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const incident = await api.createIncident({
        title,
        description,
        incident_class: incidentClass,
        severity,
        source: "ui",
        metadata: {},
      });
      toast("Incident filed. Investigation started.", "good");
      setOpen(false);
      router.push(`/incidents/${incident.id}`);
    } catch (error) {
      toast(error instanceof Error ? error.message : "Could not file the incident", "bad");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn(
          "group inline-flex h-10 items-center gap-2 rounded-lg border border-[var(--color-line)] bg-white/[0.02] px-3.5 text-[12px] font-medium text-[var(--color-muted)] transition hover:border-[#2b3a50] hover:text-[var(--color-text)]",
          className,
        )}
      >
        File incident
        <IconArrow className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
      </button>

      {open && (
        <div className="fixed inset-0 z-[75] flex items-center justify-center px-4" role="dialog" aria-modal="true">
          <button
            type="button"
            aria-label="Close"
            className="absolute inset-0 bg-black/70 backdrop-blur-sm animate-fade"
            onClick={() => setOpen(false)}
          />
          <form
            onSubmit={submit}
            className="animate-rise panel relative w-full max-w-[520px] p-5"
          >
            <div className="flex items-start justify-between">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-[var(--color-dim)]">
                  New intake
                </p>
                <h2 className="mt-1 text-lg font-semibold tracking-tight">File an incident</h2>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--color-line)] text-[var(--color-dim)]"
                aria-label="Close"
              >
                <IconX className="h-4 w-4" />
              </button>
            </div>

            <label className="mt-5 block text-[11px] font-medium text-[var(--color-muted)]">
              Title
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                required
                minLength={3}
                className="mt-1.5 h-10 w-full rounded-lg border border-[var(--color-line)] bg-[var(--color-ink)] px-3 text-[13px] text-[var(--color-text)] outline-none transition focus:border-[rgba(200,255,114,0.4)]"
              />
            </label>

            <label className="mt-3 block text-[11px] font-medium text-[var(--color-muted)]">
              What was observed
              <textarea
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                rows={3}
                className="mt-1.5 w-full resize-none rounded-lg border border-[var(--color-line)] bg-[var(--color-ink)] px-3 py-2.5 text-[13px] leading-relaxed text-[var(--color-text)] outline-none transition focus:border-[rgba(200,255,114,0.4)]"
              />
            </label>

            <div className="mt-3 grid grid-cols-2 gap-3">
              <label className="block text-[11px] font-medium text-[var(--color-muted)]">
                Class
                <select
                  value={incidentClass}
                  onChange={(event) => setIncidentClass(event.target.value)}
                  className="mt-1.5 h-10 w-full rounded-lg border border-[var(--color-line)] bg-[var(--color-ink)] px-2.5 text-[12px] text-[var(--color-text)] outline-none"
                >
                  <option value="site_outage">Site outage</option>
                  <option value="credential_exposure">Credential exposure</option>
                  <option value="phishing">Phishing</option>
                  <option value="unknown">Unknown</option>
                </select>
              </label>
              <label className="block text-[11px] font-medium text-[var(--color-muted)]">
                Severity
                <select
                  value={severity}
                  onChange={(event) => setSeverity(event.target.value)}
                  className="mt-1.5 h-10 w-full rounded-lg border border-[var(--color-line)] bg-[var(--color-ink)] px-2.5 text-[12px] text-[var(--color-text)] outline-none"
                >
                  <option value="critical">Critical</option>
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
                </select>
              </label>
            </div>

            <p className="mt-3 text-[10px] leading-relaxed text-[var(--color-dim)]">
              For a full end-to-end demo with a real fault, use the Fault studio. Manual
              intake does not inject a fault into the lab.
            </p>

            <button
              type="submit"
              disabled={busy}
              className="mt-4 flex h-10 w-full items-center justify-center gap-2 rounded-lg border border-[rgba(200,255,114,0.3)] bg-[rgba(200,255,114,0.09)] text-[13px] font-semibold text-[var(--color-acid)] transition hover:bg-[rgba(200,255,114,0.14)] disabled:opacity-60"
            >
              {busy ? (
                <span className="h-3.5 w-3.5 animate-spin-slow rounded-full border-2 border-current border-t-transparent" />
              ) : (
                <IconZap className="h-3.5 w-3.5" />
              )}
              Investigate
            </button>
          </form>
        </div>
      )}
    </>
  );
}

export type { IncidentStatus };
