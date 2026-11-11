"use client";

import { useState } from "react";
import { cn, titleCase } from "@/lib/format";
import type { ActionStatus, CaseFile } from "@/lib/types";
import { ConfidenceRing } from "@/components/data-viz";
import { RiskChip } from "@/components/chips";
import {
  IconAlert,
  IconCheck,
  IconLink,
  IconRefresh,
  IconServer,
  IconShield,
  IconX,
  IconZap,
} from "@/components/icons";

const ACTION_TONE: Record<ActionStatus, string> = {
  executed: "text-[var(--color-acid)] border-[rgba(200,255,114,0.25)] bg-[rgba(200,255,114,0.05)]",
  approved: "text-[var(--color-sky)] border-[rgba(124,196,255,0.22)] bg-[rgba(124,196,255,0.05)]",
  executing: "text-[var(--color-sky)] border-[rgba(124,196,255,0.3)] bg-[rgba(124,196,255,0.07)]",
  awaiting_approval:
    "text-[var(--color-amber)] border-[rgba(255,196,107,0.3)] bg-[rgba(255,196,107,0.06)]",
  failed: "text-[var(--color-danger)] border-[rgba(255,111,97,0.32)] bg-[rgba(255,111,97,0.07)]",
  denied: "text-[var(--color-danger)] border-[rgba(255,111,97,0.28)]",
  proposed: "text-[var(--color-dim)] border-[var(--color-line)]",
  cancelled: "text-[var(--color-dim)] border-[var(--color-line)]",
  rolled_back: "text-[var(--color-amber)] border-[rgba(255,196,107,0.28)]",
};

function actionStatusLabel(status: ActionStatus | undefined): string {
  if (!status) return "proposed";
  return titleCase(status);
}

export function PlanPanel({ caseFile }: { caseFile: CaseFile }) {
  const { plan } = caseFile;
  const [open, setOpen] = useState<string | null>(null);

  if (!plan) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--color-line)] p-5">
        <div className="flex items-center gap-2 text-[var(--color-dim)]">
          <IconZap className="h-3.5 w-3.5" />
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em]">No plan yet</p>
        </div>
        <p className="mt-2 max-w-lg text-[12px] leading-relaxed text-[var(--color-muted)]">
          A plan appears only after triage, investigation, and an independent critique. It
          must contain typed actions and verification predicates before policy will consider
          it.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="panel p-4 sm:p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-[var(--color-dim)]">
              Proposed remediation
            </p>
            <p className="mt-1.5 max-w-[62ch] text-[13.5px] leading-relaxed text-[var(--color-text)]">
              {plan.summary}
            </p>
            <p className="mono mt-2 text-[9.5px] text-[var(--color-dim)]">
              plan {plan.id.slice(0, 8)} · {plan.actions.length} action
              {plan.actions.length === 1 ? "" : "s"} · {plan.predicates.length} predicate
              {plan.predicates.length === 1 ? "" : "s"}
            </p>
          </div>
          <ConfidenceRing value={plan.confidence} size={52} />
        </div>

        <div className="mt-4 rounded-xl border border-[rgba(124,196,255,0.15)] bg-[rgba(124,196,255,0.035)] p-3.5">
          <p className="flex items-center gap-1.5 text-[9.5px] font-semibold uppercase tracking-[0.16em] text-[var(--color-sky)]">
            <IconAlert className="h-3 w-3" /> Root cause
          </p>
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--color-muted)]">
            {plan.root_cause}
          </p>
        </div>

        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          <div className="rounded-lg border border-[var(--color-line-soft)] bg-white/[0.015] p-3">
            <p className="text-[9px] uppercase tracking-[0.16em] text-[var(--color-dim)]">
              Blast radius
            </p>
            <p className="mt-1 text-[11px] text-[var(--color-muted)]">
              {plan.blast_radius.length ? plan.blast_radius.join(", ") : "single component"}
            </p>
          </div>
          <div className="rounded-lg border border-[var(--color-line-soft)] bg-white/[0.015] p-3">
            <p className="text-[9px] uppercase tracking-[0.16em] text-[var(--color-dim)]">
              Rollback
            </p>
            <p className="mt-1 text-[11px] text-[var(--color-muted)]">
              {plan.rollback_plan || "defined by the connector"}
            </p>
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        {caseFile.actions.map((action, index) => {
          const status = caseFile.action_states[action.id] as ActionStatus | undefined;
          const execution = caseFile.executions.find((item) => item.action_id === action.id);
          const expanded = open === action.id;
          const preds = plan.predicates.filter((predicate) =>
            action.predicate_ids.includes(predicate.id),
          );
          return (
            <div
              key={action.id}
              className="panel group overflow-hidden p-4 transition-colors hover:border-[#28364a]"
            >
              <div className="flex items-start gap-3">
                <span className="mono flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-[var(--color-line)] bg-[var(--color-ink)] text-[10px] text-[var(--color-dim)]">
                  {(index + 1).toString().padStart(2, "0")}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-[13px] font-semibold text-[var(--color-text)]">
                      {titleCase(action.action_type)}
                    </p>
                    <RiskChip risk={action.risk} />
                    <span
                      className={cn(
                        "rounded-full border px-2 py-0.5 text-[10px] font-medium",
                        ACTION_TONE[status ?? "proposed"],
                      )}
                    >
                      {actionStatusLabel(status)}
                    </span>
                  </div>
                  <p className="mt-1 text-[11.5px] leading-relaxed text-[var(--color-muted)]">
                    {action.description || "No description recorded."}
                  </p>
                  <p className="mono mt-1 truncate text-[10px] text-[var(--color-dim)]">
                    {action.target}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setOpen(expanded ? null : action.id)}
                  className="shrink-0 rounded-md border border-[var(--color-line)] px-2 py-1 text-[9px] text-[var(--color-dim)] transition hover:border-[#2b3a50] hover:text-[var(--color-muted)]"
                >
                  {expanded ? "hide" : "details"}
                </button>
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                {preds.map((predicate) => (
                  <span
                    key={predicate.id}
                    className="inline-flex items-center gap-1 rounded border border-[var(--color-line)] px-1.5 py-0.5 text-[9px] text-[var(--color-dim)]"
                  >
                    <IconCheck className="h-2.5 w-2.5" /> {predicate.kind}
                  </span>
                ))}
                {status === "executed" && (
                  <span className="inline-flex items-center gap-1 text-[9px] text-[var(--color-acid)]">
                    <IconCheck className="h-2.5 w-2.5" /> executed and observed
                  </span>
                )}
                {status === "awaiting_approval" && (
                  <span className="inline-flex items-center gap-1 text-[9px] text-[var(--color-amber)]">
                    <IconShield className="h-2.5 w-2.5" /> paused for a human
                  </span>
                )}
                {status === "failed" && (
                  <span className="inline-flex items-center gap-1 text-[9px] text-[var(--color-danger)]">
                    <IconX className="h-2.5 w-2.5" /> connector reported failure
                  </span>
                )}
              </div>

              {expanded && (
                <div className="animate-fade mt-3 grid gap-2 sm:grid-cols-2">
                  <div className="rounded-lg border border-[var(--color-line-soft)] bg-[#060910] p-2.5">
                    <p className="text-[9px] uppercase tracking-[0.14em] text-[var(--color-dim)]">
                      Typed parameters
                    </p>
                    <pre className="mono mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all text-[10px] leading-relaxed text-[var(--color-muted)]">
                      {JSON.stringify(action.params, null, 2)}
                    </pre>
                  </div>
                  <div className="rounded-lg border border-[var(--color-line-soft)] bg-[#060910] p-2.5">
                    <p className="text-[9px] uppercase tracking-[0.14em] text-[var(--color-dim)]">
                      Execution record
                    </p>
                    {execution ? (
                      <>
                        <p className="mt-1 flex items-center gap-1.5 text-[10px] text-[var(--color-muted)]">
                          <IconServer className="h-3 w-3" /> {execution.status}
                        </p>
                        <pre className="mono mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all text-[10px] leading-relaxed text-[var(--color-muted)]">
                          {JSON.stringify(
                            {
                              before: execution.before_state,
                              after: execution.after_state,
                              error: execution.error,
                            },
                            null,
                            2,
                          )}
                        </pre>
                      </>
                    ) : (
                      <p className="mt-1 text-[10.5px] text-[var(--color-dim)]">
                        Not executed. The executor writes before and after state here.
                      </p>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {caseFile.actions.length === 0 && (
        <p className="text-[12px] text-[var(--color-dim)]">
          The plan contains no actions, which policy will reject.
        </p>
      )}
    </div>
  );
}

export function VerificationPanel({ caseFile }: { caseFile: CaseFile }) {
  const [open, setOpen] = useState<string | null>(null);
  const required = caseFile.verifications.filter((item) => item.required);
  const passed = required.filter((item) => item.passed).length;
  const phases = [...new Set(caseFile.verifications.map((item) => item.phase))];
  const anyFailed = required.some((item) => !item.passed);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[11px] text-[var(--color-muted)]">
          {required.length === 0
            ? "Predicates have not run yet."
            : `${passed}/${required.length} required predicates passed.`}
        </p>
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px]",
            required.length === 0
              ? "border-[var(--color-line)] text-[var(--color-dim)]"
              : anyFailed
                ? "border-[rgba(255,111,97,0.3)] text-[var(--color-danger)]"
                : "border-[rgba(200,255,114,0.25)] text-[var(--color-acid)]",
          )}
        >
          {required.length === 0 ? (
            <IconRefresh className="h-3 w-3" />
          ) : anyFailed ? (
            <IconX className="h-3 w-3" />
          ) : (
            <IconCheck className="h-3 w-3" />
          )}
          {required.length === 0 ? "not run" : anyFailed ? "failed" : "verified"}
        </span>
      </div>

      {phases.map((phase) => {
        const items = caseFile.verifications.filter((item) => item.phase === phase);
        return (
          <div key={phase}>
            <p className="mb-1.5 flex items-center gap-2 text-[9.5px] font-semibold uppercase tracking-[0.16em] text-[var(--color-dim)]">
              <IconLink className="h-3 w-3" />
              {phase === "t0" ? "T+0 · immediately after execution" : `${phase} · stability window`}
            </p>
            <div className="flex flex-col gap-1.5">
              {items.map((verification) => {
                const expanded = open === verification.id;
                return (
                  <div
                    key={verification.id}
                    className={cn(
                      "rounded-lg border p-3 transition-colors",
                      verification.passed
                        ? "border-[rgba(200,255,114,0.14)] bg-[rgba(200,255,114,0.025)]"
                        : "border-[rgba(255,111,97,0.22)] bg-[rgba(255,111,97,0.04)]",
                    )}
                  >
                    <div className="flex items-start gap-2.5">
                      <span
                        className={cn(
                          "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
                          verification.passed
                            ? "border-[rgba(200,255,114,0.4)] text-[var(--color-acid)]"
                            : "border-[rgba(255,111,97,0.45)] text-[var(--color-danger)]",
                        )}
                      >
                        {verification.passed ? (
                          <IconCheck className="h-2.5 w-2.5" />
                        ) : (
                          <IconX className="h-2.5 w-2.5" />
                        )}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="text-[11.5px] leading-snug text-[var(--color-text)]">
                          {verification.kind.replace(/_/g, " ")}
                          <span className="ml-2 text-[9.5px] text-[var(--color-dim)]">
                            {verification.required ? "required" : "optional"}
                          </span>
                        </p>
                        <p className="mt-0.5 truncate text-[10px] text-[var(--color-dim)]">
                          {verification.error ??
                            describeObserved(verification.observed) ??
                            "observed value recorded"}
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={() => setOpen(expanded ? null : verification.id)}
                        className="shrink-0 text-[9px] text-[var(--color-dim)] hover:text-[var(--color-muted)]"
                      >
                        {expanded ? "hide" : "observed"}
                      </button>
                    </div>
                    {expanded && (
                      <pre className="mono animate-fade mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-md border border-[var(--color-line-soft)] bg-[#060910] p-2 text-[10px] leading-relaxed text-[var(--color-muted)]">
                        {JSON.stringify(verification.observed, null, 2)}
                      </pre>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function describeObserved(observed: Record<string, unknown>): string | null {
  if (typeof observed.p95_ms === "number") return `p95 ${observed.p95_ms}ms`;
  if (Array.isArray(observed.statuses)) return `statuses ${observed.statuses.join(", ")}`;
  if (typeof observed.status === "number") return `status ${observed.status}`;
  if (typeof observed.healthy === "boolean") return observed.healthy ? "healthy" : "unhealthy";
  if (typeof observed.equals === "boolean") return `equals ${observed.equals}`;
  const key = Object.keys(observed)[0];
  if (!key) return null;
  const value = observed[key];
  if (typeof value === "object") return null;
  return `${key}: ${String(value)}`;
}
