"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import {
  classLabel,
  cn,
  dateTime,
  durationBetween,
  percent,
  relativeTime,
} from "@/lib/format";
import type { CaseFile } from "@/lib/types";
import { useCaseStream } from "@/hooks/use-case-stream";
import { useToast } from "@/components/toast";
import { FileStepper } from "@/components/case/file-stepper";
import { PlanPanel, VerificationPanel } from "@/components/case/plan-panel";
import { EvidenceLedger } from "@/components/case/evidence-ledger";
import { Timeline } from "@/components/case/timeline";
import { ApprovalPanel } from "@/components/case/approval-panel";
import {
  AgentRunsPanel,
  ChainPanel,
  FindingsPanel,
  NotesPanel,
} from "@/components/case/integrity-panel";
import { SeverityChip } from "@/components/chips";
import { StatusBadge } from "@/components/status-badge";
import { ConfidenceRing } from "@/components/data-viz";
import { EmptyState, SectionLabel, Skeleton } from "@/components/primitives";
import {
  IconAlert,
  IconArrow,
  IconCheck,
  IconClock,
  IconDatabase,
  IconRefresh,
  IconSearch,
  IconShield,
  IconZap,
} from "@/components/icons";

export function CaseFileClient({ incidentId }: { incidentId: string }) {
  const toast = useToast();
  const [busy, setBusy] = useState<"retry" | "verify" | null>(null);
  const { caseFile, connected, fallback, error, reload } = useCaseStream(incidentId);

  const retry = async () => {
    setBusy("retry");
    try {
      await api.retry(incidentId);
      toast("New investigation started. Older plans stay in the ledger.", "good");
      reload();
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : "Retry failed", "bad");
    } finally {
      setBusy(null);
    }
  };

  const verifyNow = async () => {
    setBusy("verify");
    try {
      await api.verifyNow(incidentId, "t0");
      toast("Required predicates executed now.", "good");
      reload();
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : "Verification failed", "bad");
    } finally {
      setBusy(null);
    }
  };

  if (!caseFile) {
    if (error) {
      return (
        <div className="mx-auto max-w-3xl px-5 py-16">
          <EmptyState
            icon={<IconAlert className="h-5 w-5" />}
            title="This case file is not available"
            body={error}
            action={
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={reload}
                  className="inline-flex h-9 items-center gap-2 rounded-lg border border-[var(--color-line)] px-3 text-[12px] text-[var(--color-muted)]"
                >
                  <IconRefresh className="h-3.5 w-3.5" /> Try again
                </button>
                <Link
                  href="/"
                  className="inline-flex h-9 items-center gap-2 rounded-lg border border-[rgba(200,255,114,0.3)] bg-[rgba(200,255,114,0.07)] px-3 text-[12px] font-medium text-[var(--color-acid)]"
                >
                  Command deck <IconArrow className="h-3 w-3" />
                </Link>
              </div>
            }
          />
        </div>
      );
    }
    return (
      <div className="mx-auto max-w-[1480px] px-4 py-6 sm:px-6 lg:px-8">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="mt-4 h-24 w-full" />
        <div className="mt-5 grid gap-5 lg:grid-cols-[1.55fr_0.95fr]">
          <div className="space-y-4">
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-48 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
          <div className="space-y-4">
            <Skeleton className="h-44 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        </div>
      </div>
    );
  }

  const { incident } = caseFile;
  const pending = caseFile.approvals.filter((approval) => approval.status === "pending");
  const required = caseFile.verifications.filter((item) => item.required);
  const passed = required.filter((item) => item.passed).length;
  const chainValid = caseFile.chain.evidence_valid && caseFile.chain.timeline_valid;
  const active = [
    "intake",
    "investigating",
    "awaiting_approval",
    "awaiting_manual",
    "executing",
    "verifying",
  ].includes(incident.status);
  const canRetry = ["failed", "reopened"].includes(incident.status);

  return (
    <div className="mx-auto max-w-[1480px] px-4 pb-12 pt-5 sm:px-6 lg:px-8">
      <nav className="flex flex-wrap items-center gap-2 text-[11px] text-[var(--color-dim)]">
        <Link href="/" className="underline-grow hover:text-[var(--color-muted)]">
          Command deck
        </Link>
        <span className="text-[var(--color-line)]">/</span>
        <span className="text-[var(--color-muted)]">Case file</span>
        <span className="text-[var(--color-line)]">/</span>
        <span className="mono">{incident.id.slice(0, 10)}</span>
        <span className="ml-auto flex items-center gap-2">
          <span
            className={cn(
              "status-dot",
              connected ? "text-[var(--color-acid)] live" : "text-[var(--color-danger)]",
            )}
          />
          <span className="mono text-[10px]">
            {connected
              ? fallback
                ? "polling"
                : "streaming"
              : "offline"}
          </span>
          <span className="text-[var(--color-line)]">·</span>
          <span className="text-[10px]">updated {relativeTime(incident.updated_at)}</span>
        </span>
      </nav>

      <header className="panel mt-3 overflow-hidden">
        <div className="relative p-4 sm:p-5">
          <div
            className="pointer-events-none absolute inset-0 opacity-20"
            style={{
              backgroundImage:
                "radial-gradient(ellipse at 88% 0%, rgba(200,255,114,0.16), transparent 58%)",
            }}
          />
          <div className="relative flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 max-w-[70ch]">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={incident.status} />
                <SeverityChip severity={incident.severity} />
                <span className="rounded-md border border-[var(--color-line)] px-2 py-0.5 text-[10px] text-[var(--color-muted)]">
                  {classLabel(incident.incident_class)}
                </span>
                <span className="mono text-[10px] text-[var(--color-dim)]">
                  opened {dateTime(incident.created_at)}
                </span>
              </div>
              <h1 className="mt-3 text-balance text-[clamp(1.4rem,2.6vw,1.95rem)] font-semibold leading-tight tracking-[-0.03em]">
                {incident.title}
              </h1>
              {incident.description && (
                <p className="mt-2 max-w-[72ch] text-[12.5px] leading-relaxed text-[var(--color-muted)]">
                  {incident.description}
                </p>
              )}
              <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[10px] text-[var(--color-dim)]">
                <span className="flex items-center gap-1.5">
                  <IconClock className="h-3 w-3" />
                  {durationBetween(incident.created_at, incident.resolved_at)}
                  {incident.resolved_at ? " to resolution" : " open"}
                </span>
                <span className="flex items-center gap-1.5">
                  <IconDatabase className="h-3 w-3" />
                  {caseFile.evidence.length} evidence
                </span>
                <span className="flex items-center gap-1.5">
                  <IconShield className="h-3 w-3" />
                  {caseFile.approvals.length} approval
                  {caseFile.approvals.length === 1 ? "" : "s"}
                </span>
                <span className="flex items-center gap-1.5">
                  <IconCheck className="h-3 w-3" />
                  {passed}/{required.length || "—"} predicates
                </span>
                <span className="mono">source {incident.source}</span>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <div className="hidden text-right sm:block">
                <p className="text-[9px] uppercase tracking-[0.16em] text-[var(--color-dim)]">
                  confidence
                </p>
                <p className="mono text-[12px] text-[var(--color-text)]">
                  {percent(incident.confidence)}
                </p>
              </div>
              <ConfidenceRing value={incident.confidence} size={54} />
            </div>
          </div>

          <div className="relative mt-4 flex flex-wrap items-center gap-2 border-t border-[var(--color-line-soft)] pt-3.5">
            {pending.length > 0 && (
              <a
                href="#decision"
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-[rgba(255,196,107,0.3)] bg-[rgba(255,196,107,0.07)] px-3 text-[11px] font-medium text-[var(--color-amber)] transition hover:bg-[rgba(255,196,107,0.12)]"
              >
                <span className="status-dot wait" />
                {pending.length} decision{pending.length === 1 ? "" : "s"} waiting
              </a>
            )}
            <button
              type="button"
              onClick={() => void verifyNow()}
              disabled={busy !== null}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-[var(--color-line)] px-3 text-[11px] text-[var(--color-muted)] transition hover:border-[#2b3a50] hover:text-[var(--color-text)] disabled:opacity-50"
            >
              {busy === "verify" ? (
                <span className="h-3 w-3 animate-spin-slow rounded-full border border-current border-t-transparent" />
              ) : (
                <IconCheck className="h-3 w-3" />
              )}
              Verify now
            </button>
            {canRetry && (
              <button
                type="button"
                onClick={() => void retry()}
                disabled={busy !== null}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-[var(--color-line)] px-3 text-[11px] text-[var(--color-muted)] transition hover:border-[#2b3a50] hover:text-[var(--color-text)] disabled:opacity-50"
              >
                {busy === "retry" ? (
                  <span className="h-3 w-3 animate-spin-slow rounded-full border border-current border-t-transparent" />
                ) : (
                  <IconRefresh className="h-3 w-3" />
                )}
                New plan
              </button>
            )}
            <button
              type="button"
              onClick={reload}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-[var(--color-line)] px-3 text-[11px] text-[var(--color-muted)] transition hover:border-[#2b3a50] hover:text-[var(--color-text)]"
            >
              <IconRefresh className="h-3 w-3" />
              Refresh
            </button>
            <span className="ml-auto flex items-center gap-1.5 text-[10px] text-[var(--color-dim)]">
              <span className={cn("status-dot", chainValid ? "text-[var(--color-acid)]" : "text-[var(--color-danger)]")} />
              {chainValid ? "ledger sealed" : "ledger unverified"}
            </span>
          </div>
        </div>
      </header>

      <div className="mt-4 grid items-start gap-4 lg:grid-cols-[1.55fr_0.95fr] xl:gap-5">
        <div className="flex min-w-0 flex-col gap-4">
          <FileStepper caseFile={caseFile} />

          {(incident.summary || incident.root_cause) && (
            <section className="panel p-4 sm:p-5">
              <SectionLabel>Assessment</SectionLabel>
              {incident.summary && (
                <p className="mt-2.5 max-w-[80ch] text-[13px] leading-relaxed text-[var(--color-text)]">
                  {incident.summary}
                </p>
              )}
              {incident.root_cause && (
                <p className="mt-2 max-w-[80ch] rounded-lg border border-[rgba(124,196,255,0.14)] bg-[rgba(124,196,255,0.03)] p-3 text-[12px] leading-relaxed text-[var(--color-muted)]">
                  <span className="mr-1.5 text-[9px] font-semibold uppercase tracking-[0.16em] text-[var(--color-sky)]">
                    Root cause
                  </span>
                  {incident.root_cause}
                </p>
              )}
            </section>
          )}

          <section className="panel p-4 sm:p-5">
            <div className="flex items-center justify-between gap-3">
              <SectionLabel className="flex-1">
                <span className="inline-flex items-center gap-1.5">
                  <IconSearch className="h-3 w-3" /> Investigator findings
                </span>
              </SectionLabel>
              <span className="mono ml-3 text-[10px] text-[var(--color-dim)]">
                {caseFile.findings.length} claim
                {caseFile.findings.length === 1 ? "" : "s"} groups
              </span>
            </div>
            <div className="mt-3">
              <FindingsPanel caseFile={caseFile} />
            </div>
          </section>

          <section className="panel p-4 sm:p-5">
            <div className="flex items-center justify-between gap-3">
              <SectionLabel className="flex-1">
                <span className="inline-flex items-center gap-1.5">
                  <IconZap className="h-3 w-3" /> Remediation plan
                </span>
              </SectionLabel>
              <span className="mono ml-3 text-[10px] text-[var(--color-dim)]">
                {caseFile.plan ? `plan ${caseFile.plan.id.slice(0, 8)}` : "none"}
              </span>
            </div>
            <div className="mt-3">
              <PlanPanel caseFile={caseFile} />
            </div>
          </section>

          <section className="panel p-4 sm:p-5">
            <div className="flex items-center justify-between gap-3">
              <SectionLabel className="flex-1">
                <span className="inline-flex items-center gap-1.5">
                  <IconShield className="h-3 w-3" /> Verification
                </span>
              </SectionLabel>
              <span className="mono ml-3 text-[10px] text-[var(--color-dim)]">
                {passed}/{required.length || 0} required
              </span>
            </div>
            <div className="mt-3">
              <VerificationPanel caseFile={caseFile} />
            </div>
          </section>

          <section className="panel p-4 sm:p-5">
            <div className="flex items-center justify-between gap-3">
              <SectionLabel className="flex-1">
                <span className="inline-flex items-center gap-1.5">
                  <IconDatabase className="h-3 w-3" /> Evidence ledger
                </span>
              </SectionLabel>
              <span className="mono ml-3 text-[10px] text-[var(--color-dim)]">
                {caseFile.evidence.length} items
              </span>
            </div>
            <div className="mt-3">
              <EvidenceLedger evidence={caseFile.evidence} />
            </div>
          </section>

          <section className="panel p-4 sm:p-5">
            <div className="flex items-center justify-between gap-3">
              <SectionLabel className="flex-1">
                <span className="inline-flex items-center gap-1.5">
                  <IconClock className="h-3 w-3" /> Timeline
                </span>
              </SectionLabel>
              <span className="mono ml-3 text-[10px] text-[var(--color-dim)]">
                {caseFile.timeline.length} events
              </span>
            </div>
            <div className="mt-3">
              <Timeline events={caseFile.timeline} />
            </div>
          </section>
        </div>

        <aside className="flex min-w-0 flex-col gap-4 lg:sticky lg:top-[84px]">
          <section id="decision" className="panel p-4">
            <div className="flex items-center justify-between gap-3">
              <SectionLabel className="flex-1">Decision</SectionLabel>
              {pending.length > 0 && (
                <span className="mono ml-3 rounded-full border border-[rgba(255,196,107,0.28)] px-2 py-0.5 text-[9px] text-[var(--color-amber)]">
                  {pending.length} pending
                </span>
              )}
            </div>
            <div className="mt-3">
              <ApprovalPanel caseFile={caseFile} onChanged={reload} />
            </div>
          </section>

          <section className="panel p-4">
            <SectionLabel>Integrity</SectionLabel>
            <div className="mt-3">
              <ChainPanel caseFile={caseFile} />
            </div>
          </section>

          <section className="panel p-4">
            <SectionLabel>Model calls</SectionLabel>
            <div className="mt-3">
              <AgentRunsPanel caseFile={caseFile} />
            </div>
          </section>

          <section className="panel p-4">
            <SectionLabel>Operator notes</SectionLabel>
            <div className="mt-3">
              <NotesPanel caseFile={caseFile} onChanged={reload} />
            </div>
          </section>

          <section className="panel p-4">
            <SectionLabel>Record</SectionLabel>
            <dl className="mt-2.5 flex flex-col gap-0.5 text-[11px]">
              <Record label="Incident" value={incident.id} mono />
              <Record label="Tenant" value={incident.tenant_id} mono />
              <Record label="Source" value={incident.source} />
              <Record label="Created" value={dateTime(incident.created_at)} />
              <Record label="Updated" value={dateTime(incident.updated_at)} />
              <Record label="Resolved" value={dateTime(incident.resolved_at)} />
              {incident.dedupe_key && (
                <Record label="Dedupe" value={incident.dedupe_key} mono />
              )}
            </dl>
            <p className="mt-3 flex items-start gap-1.5 border-t border-[var(--color-line-soft)] pt-2.5 text-[10px] leading-relaxed text-[var(--color-dim)]">
              <IconCheck className="mt-0.5 h-3 w-3 shrink-0 text-[var(--color-acid)]" />
              A resolved case has a stored plan, an executed action, passing T+0
              predicates, and a clean stability window.
            </p>
          </section>
        </aside>
      </div>
    </div>
  );
}

function Record({
  label,
  value,
  mono,
}: {
  label: string;
  value: string | null;
  mono?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-[var(--color-line-soft)] py-1.5 last:border-0">
      <dt className="shrink-0 text-[var(--color-dim)]">{label}</dt>
      <dd
        className={cn(
          "truncate text-right text-[var(--color-muted)]",
          mono && "mono text-[10px]",
        )}
        title={value ?? undefined}
      >
        {value ?? "—"}
      </dd>
    </div>
  );
}
