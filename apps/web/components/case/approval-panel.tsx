"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  clockTime,
  cn,
  formatCountdown,
  percent,
  secondsLeft,
  titleCase,
} from "@/lib/format";
import type { ApprovalRequest, CaseFile } from "@/lib/types";
import { useToast } from "@/components/toast";
import { RiskChip } from "@/components/chips";
import {
  IconAlert,
  IconCheck,
  IconClock,
  IconLock,
  IconShield,
  IconX,
} from "@/components/icons";

const STATUS_STYLE: Record<string, string> = {
  pending: "text-[var(--color-amber)] border-[rgba(255,196,107,0.28)] bg-[rgba(255,196,107,0.06)]",
  approved: "text-[var(--color-acid)] border-[rgba(200,255,114,0.24)] bg-[rgba(200,255,114,0.05)]",
  rejected: "text-[var(--color-danger)] border-[rgba(255,111,97,0.28)] bg-[rgba(255,111,97,0.06)]",
  expired: "text-[var(--color-dim)] border-[var(--color-line)] bg-white/[0.02]",
};

function PendingApproval({
  approval,
  caseFile,
  onChanged,
}: {
  approval: ApprovalRequest;
  caseFile: CaseFile;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [remaining, setRemaining] = useState(() => secondsLeft(approval.expires_at));

  useEffect(() => {
    setRemaining(secondsLeft(approval.expires_at));
    const timer = window.setInterval(
      () => setRemaining(secondsLeft(approval.expires_at)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [approval.expires_at]);

  const action = caseFile.plan?.actions.find((item) => item.id === approval.action_id);
  const predicates =
    caseFile.plan?.predicates.filter((predicate) =>
      action?.predicate_ids.includes(predicate.id),
    ) ?? [];

  const decide = async (approved: boolean) => {
    setBusy(approved ? "approve" : "reject");
    try {
      await api.decideApproval(caseFile.incident.id, approval.id, approved, comment);
      toast(
        approved
          ? "Approved. Execution starts now and verification follows."
          : "Rejected. Nothing executes and the incident stays open.",
        approved ? "good" : "bad",
      );
      setComment("");
      onChanged();
    } catch (error) {
      toast(error instanceof Error ? error.message : "Decision failed", "bad");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="corner-cut rounded-xl border border-[rgba(255,196,107,0.24)] bg-[linear-gradient(180deg,rgba(255,196,107,0.05),rgba(10,14,22,0.4))] p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="status-dot wait text-[var(--color-amber)]" />
          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--color-amber)]">
            Approval required
          </p>
        </div>
        <span
          className={cn(
            "mono flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px]",
            remaining > 0
              ? "border-[rgba(255,196,107,0.28)] text-[var(--color-amber)]"
              : "border-[rgba(255,111,97,0.3)] text-[var(--color-danger)]",
          )}
        >
          <IconClock className="h-3 w-3" />
          {formatCountdown(remaining)}
        </span>
      </div>

      {action ? (
        <div className="mt-3 rounded-lg border border-[var(--color-line-soft)] bg-[#070b12] p-3">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[12.5px] font-semibold text-[var(--color-text)]">
              {titleCase(action.action_type)}
            </p>
            <RiskChip risk={action.risk} />
          </div>
          <p className="mt-1 text-[11.5px] leading-relaxed text-[var(--color-muted)]">
            {action.description || "The planner did not record a description."}
          </p>
          <p className="mono mt-1 truncate text-[10px] text-[var(--color-dim)]">
            {action.target}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {predicates.map((predicate) => (
              <span
                key={predicate.id}
                className="inline-flex items-center gap-1 rounded border border-[var(--color-line)] px-1.5 py-0.5 text-[9px] text-[var(--color-dim)]"
              >
                <IconLinkMini /> {predicate.kind}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <p className="mt-3 rounded-lg border border-[var(--color-line-soft)] bg-[#070b12] p-3 text-[11.5px] text-[var(--color-muted)]">
          The approval is scoped to action{" "}
          <span className="mono text-[var(--color-dim)]">{approval.action_id.slice(0, 8)}</span>,
          which is not in the latest plan. It cannot execute.
        </p>
      )}

      <label className="mt-3 block text-[10px] font-medium text-[var(--color-dim)]">
        Note for the ledger (optional)
        <input
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          maxLength={2000}
          placeholder="Why you decided this"
          className="mt-1 h-9 w-full rounded-lg border border-[var(--color-line)] bg-[var(--color-ink)] px-2.5 text-[12px] text-[var(--color-text)] outline-none transition placeholder:text-[var(--color-dim)]/70 focus:border-[rgba(255,196,107,0.4)]"
        />
      </label>

      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={busy !== null || remaining <= 0}
          onClick={() => void decide(true)}
          className="group flex h-9 flex-1 items-center justify-center gap-1.5 rounded-lg border border-[rgba(200,255,114,0.3)] bg-[rgba(200,255,114,0.09)] text-[12px] font-semibold text-[var(--color-acid)] transition hover:bg-[rgba(200,255,114,0.15)] disabled:cursor-not-allowed disabled:opacity-45"
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
          disabled={busy !== null || remaining <= 0}
          onClick={() => void decide(false)}
          className="flex h-9 items-center justify-center gap-1.5 rounded-lg border border-[var(--color-line)] px-3.5 text-[12px] text-[var(--color-muted)] transition hover:border-[rgba(255,111,97,0.35)] hover:text-[var(--color-danger)] disabled:cursor-not-allowed disabled:opacity-45"
        >
          {busy === "reject" ? (
            <span className="h-3 w-3 animate-spin-slow rounded-full border border-current border-t-transparent" />
          ) : (
            <IconX className="h-3.5 w-3.5" />
          )}
          Reject
        </button>
      </div>

      <p className="mt-2.5 flex items-start gap-1.5 text-[10px] leading-relaxed text-[var(--color-dim)]">
        <IconLock className="mt-0.5 h-2.5 w-2.5 shrink-0" />
        The model cannot approve its own action. Approval is scoped to the current plan
        and expires automatically.
      </p>
    </div>
  );
}

function IconLinkMini() {
  return (
    <svg viewBox="0 0 16 16" className="h-2.5 w-2.5" fill="none" stroke="currentColor" strokeWidth="1.4">
      <path d="M6.5 9.5a2.5 2.5 0 0 0 3.5 0l1.8-1.8a2.5 2.5 0 0 0-3.5-3.5L7.4 5" />
      <path d="M9.5 6.5a2.5 2.5 0 0 0-3.5 0L4.2 8.3a2.5 2.5 0 0 0 3.5 3.5l.9-.9" />
    </svg>
  );
}

export function ApprovalPanel({
  caseFile,
  onChanged,
}: {
  caseFile: CaseFile;
  onChanged: () => void;
}) {
  const pending = caseFile.approvals.filter((approval) => approval.status === "pending");
  const history = caseFile.approvals
    .filter((approval) => approval.status !== "pending")
    .sort(
      (a, b) =>
        new Date(b.approved_at ?? b.requested_at).getTime() -
        new Date(a.approved_at ?? a.requested_at).getTime(),
    );

  if (caseFile.approvals.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--color-line)] p-4">
        <div className="flex items-center gap-2 text-[var(--color-muted)]">
          <IconShield className="h-3.5 w-3.5" />
          <p className="text-[10.5px] font-semibold uppercase tracking-[0.16em]">
            No approval needed
          </p>
        </div>
        <p className="mt-2 text-[11.5px] leading-relaxed text-[var(--color-muted)]">
          Policy allowed every action in this plan, or the plan has not reached the gate
          yet. R2 and R3 actions stop here for a human decision.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {pending.map((approval) => (
        <PendingApproval
          key={approval.id}
          approval={approval}
          caseFile={caseFile}
          onChanged={onChanged}
        />
      ))}

      {pending.length === 0 && (
        <div className="flex items-center gap-2 rounded-xl border border-[var(--color-line)] bg-white/[0.015] px-3 py-2.5 text-[11px] text-[var(--color-muted)]">
          <IconShield className="h-3.5 w-3.5 text-[var(--color-acid)]" />
          No decision is waiting on a human.
        </div>
      )}

      {history.length > 0 && (
        <div>
          <p className="mb-1.5 text-[9.5px] font-semibold uppercase tracking-[0.16em] text-[var(--color-dim)]">
            Decision history
          </p>
          <div className="flex flex-col gap-1.5">
            {history.map((approval) => (
              <div
                key={approval.id}
                className="flex items-start gap-2.5 rounded-lg border border-[var(--color-line-soft)] bg-white/[0.012] px-3 py-2.5"
              >
                <span
                  className={cn(
                    "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
                    approval.status === "approved"
                      ? "border-[rgba(200,255,114,0.35)] text-[var(--color-acid)]"
                      : approval.status === "rejected"
                        ? "border-[rgba(255,111,97,0.4)] text-[var(--color-danger)]"
                        : "border-[var(--color-line)] text-[var(--color-dim)]",
                  )}
                >
                  {approval.status === "approved" ? (
                    <IconCheck className="h-2.5 w-2.5" />
                  ) : approval.status === "rejected" ? (
                    <IconX className="h-2.5 w-2.5" />
                  ) : (
                    <IconClock className="h-2.5 w-2.5" />
                  )}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span
                      className={cn(
                        "rounded-full border px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide",
                        STATUS_STYLE[approval.status],
                      )}
                    >
                      {approval.status}
                    </span>
                    <span className="text-[10.5px] text-[var(--color-muted)]">
                      {approval.approved_by ?? "unattributed"}
                    </span>
                    <span className="text-[9.5px] text-[var(--color-dim)]">
                      {clockTime(approval.approved_at ?? approval.requested_at)}
                    </span>
                  </div>
                  {approval.comment && (
                    <p className="mt-1 text-[10.5px] leading-relaxed text-[var(--color-muted)]">
                      “{approval.comment}”
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function ApprovalSummary({ caseFile }: { caseFile: CaseFile }) {
  const approved = caseFile.approvals.filter((item) => item.status === "approved").length;
  const rejected = caseFile.approvals.filter((item) => item.status === "rejected").length;
  if (caseFile.approvals.length === 0) return null;
  return (
    <p className="flex flex-wrap items-center gap-2 text-[10px] text-[var(--color-dim)]">
      <IconAlert className="h-3 w-3 text-[var(--color-amber)]" />
      {approved} approved · {rejected} rejected ·{" "}
      {percent(caseFile.plan?.confidence ?? null)} planner confidence
    </p>
  );
}
