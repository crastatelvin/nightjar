"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import {
  clockTime,
  cn,
  percent,
  relativeTime,
  shortHash,
  titleCase,
} from "@/lib/format";
import type { CaseFile, ClaimStatus } from "@/lib/types";
import { useToast } from "@/components/toast";
import { ConfidenceRing } from "@/components/data-viz";
import {
  IconAlert,
  IconCheck,
  IconDatabase,
  IconLink,
  IconLock,
  IconMail,
  IconSearch,
  IconSend,
  IconServer,
  IconSpark,
  IconX,
} from "@/components/icons";

export function ChainPanel({ caseFile }: { caseFile: CaseFile }) {
  const { chain } = caseFile;
  const valid = chain.evidence_valid && chain.timeline_valid;
  const evidence = [...caseFile.evidence].slice(-6);
  const timeline = [...caseFile.timeline].slice(-6);

  return (
    <div
      className={cn(
        "rounded-xl border p-4",
        valid
          ? "border-[rgba(200,255,114,0.18)] bg-[rgba(200,255,114,0.02)]"
          : "border-[rgba(255,111,97,0.28)] bg-[rgba(255,111,97,0.045)]",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "flex h-7 w-7 items-center justify-center rounded-lg border",
              valid
                ? "border-[rgba(200,255,114,0.3)] text-[var(--color-acid)]"
                : "border-[rgba(255,111,97,0.35)] text-[var(--color-danger)]",
            )}
          >
            {valid ? <IconLock className="h-3.5 w-3.5" /> : <IconAlert className="h-3.5 w-3.5" />}
          </span>
          <div>
            <p className="text-[12px] font-semibold text-[var(--color-text)]">
              {valid ? "Ledger sealed" : "Chain integrity failed"}
            </p>
            <p className="text-[10px] text-[var(--color-dim)]">
              SHA-256 · previous-hash chaining
            </p>
          </div>
        </div>
        <span
          className={cn(
            "mono rounded-full border px-2 py-0.5 text-[9px] uppercase tracking-wide",
            valid
              ? "border-[rgba(200,255,114,0.24)] text-[var(--color-acid)]"
              : "border-[rgba(255,111,97,0.3)] text-[var(--color-danger)]",
          )}
        >
          {valid ? "verified" : "broken"}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <div className="rounded-lg border border-[var(--color-line-soft)] bg-[#070b12] p-2.5">
          <p className="text-[9px] uppercase tracking-[0.14em] text-[var(--color-dim)]">
            Evidence
          </p>
          <p
            className={cn(
              "mono mt-1 flex items-center gap-1.5 text-[12px]",
              chain.evidence_valid ? "text-[var(--color-acid)]" : "text-[var(--color-danger)]",
            )}
          >
            {chain.evidence_valid ? (
              <IconCheck className="h-3 w-3" />
            ) : (
              <IconX className="h-3 w-3" />
            )}
            {chain.evidence_checked} items
          </p>
        </div>
        <div className="rounded-lg border border-[var(--color-line-soft)] bg-[#070b12] p-2.5">
          <p className="text-[9px] uppercase tracking-[0.14em] text-[var(--color-dim)]">
            Timeline
          </p>
          <p
            className={cn(
              "mono mt-1 flex items-center gap-1.5 text-[12px]",
              chain.timeline_valid ? "text-[var(--color-acid)]" : "text-[var(--color-danger)]",
            )}
          >
            {chain.timeline_valid ? (
              <IconCheck className="h-3 w-3" />
            ) : (
              <IconX className="h-3 w-3" />
            )}
            {chain.timeline_checked} events
          </p>
        </div>
      </div>

      {chain.broken_at && (
        <p className="mono mt-2 rounded-md border border-[rgba(255,111,97,0.3)] bg-[rgba(255,111,97,0.06)] px-2 py-1.5 text-[10px] text-[var(--color-danger)]">
          broken at {chain.broken_at}
        </p>
      )}

      <div className="mt-3">
        <p className="mb-1.5 text-[9px] uppercase tracking-[0.14em] text-[var(--color-dim)]">
          Latest links
        </p>
        <div className="flex flex-col gap-1">
          {evidence.length === 0 && (
            <p className="text-[10.5px] text-[var(--color-dim)]">No evidence recorded yet.</p>
          )}
          {evidence.map((item) => (
            <div key={item.id} className="flex items-center gap-1.5 text-[9.5px]">
              <IconDatabase className="h-2.5 w-2.5 shrink-0 text-[var(--color-line)]" />
              <span className="mono truncate text-[var(--color-dim)]" title={item.prev_hash}>
                {shortHash(item.prev_hash, 6)}
              </span>
              <IconLink className="h-2.5 w-2.5 shrink-0 text-[var(--color-line)]" />
              <span
                className="mono truncate text-[var(--color-muted)]"
                title={item.hash}
              >
                {shortHash(item.hash, 8)}
              </span>
              <span className="ml-auto shrink-0 text-[8.5px] text-[var(--color-dim)]/80">
                {item.kind}
              </span>
            </div>
          ))}
        </div>
      </div>

      {timeline.length > 0 && (
        <p className="mono mt-2.5 border-t border-[var(--color-line-soft)] pt-2 text-[9px] text-[var(--color-dim)]">
          timeline head {shortHash(timeline[timeline.length - 1]?.hash, 14)} · seq{" "}
          {timeline[timeline.length - 1]?.seq}
        </p>
      )}
    </div>
  );
}

const ROLE_TONE: Record<string, string> = {
  triage: "text-[var(--color-sky)]",
  investigator: "text-[var(--color-mint)]",
  critic: "text-[var(--color-violet)]",
  planner: "text-[var(--color-acid)]",
  remediation: "text-[var(--color-acid)]",
};

function RoleIcon({ role }: { role: string }) {
  if (role === "triage") return <IconSpark className="h-3 w-3" />;
  if (role === "critic") return <IconSearch className="h-3 w-3" />;
  if (role === "investigator") return <IconDatabase className="h-3 w-3" />;
  return <IconServer className="h-3 w-3" />;
}

export function AgentRunsPanel({ caseFile }: { caseFile: CaseFile }) {
  const [open, setOpen] = useState<string | null>(null);
  const runs = [...caseFile.agent_runs].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
  );
  const totalCost = runs.reduce((sum, run) => sum + run.cost_usd, 0);
  const totalTokens = runs.reduce(
    (sum, run) => sum + run.input_tokens + run.output_tokens,
    0,
  );
  const errors = runs.filter((run) => run.error !== null).length;

  if (runs.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-[var(--color-line)] p-4 text-[11.5px] text-[var(--color-muted)]">
        No model calls yet. Each role records its provider, model, latency, and token
        usage here; the control plane does not depend on them.
      </p>
    );
  }

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[9.5px] text-[var(--color-dim)]">
        <span className="mono">{runs.length} calls</span>
        <span>·</span>
        <span className="mono">{totalTokens.toLocaleString()} tokens</span>
        <span>·</span>
        <span className="mono">${totalCost.toFixed(4)}</span>
        {errors > 0 && (
          <>
            <span>·</span>
            <span className="text-[var(--color-danger)]">{errors} failed</span>
          </>
        )}
      </div>
      <div className="flex flex-col gap-1.5">
        {runs.map((run) => {
          const expanded = open === run.id;
          const tone = ROLE_TONE[run.role] ?? "text-[var(--color-muted)]";
          return (
            <div
              key={run.id}
              className={cn(
                "rounded-lg border px-3 py-2.5",
                run.error
                  ? "border-[rgba(255,111,97,0.25)] bg-[rgba(255,111,97,0.04)]"
                  : "border-[var(--color-line-soft)] bg-white/[0.012]",
              )}
            >
              <div className="flex items-center gap-2">
                <span className={cn("flex h-5 w-5 items-center justify-center rounded border border-[var(--color-line)]", tone)}>
                  <RoleIcon role={run.role} />
                </span>
                <span className="text-[11px] font-medium text-[var(--color-text)]">
                  {titleCase(run.role)}
                </span>
                <span className="mono truncate text-[9px] text-[var(--color-dim)]">
                  {run.provider}/{run.model}
                </span>
                <button
                  type="button"
                  onClick={() => setOpen(expanded ? null : run.id)}
                  className="ml-auto shrink-0 text-[9px] text-[var(--color-dim)] hover:text-[var(--color-muted)]"
                >
                  {expanded ? "hide" : "output"}
                </button>
              </div>
              <div className="mono mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[9px] text-[var(--color-dim)]">
                <span>{run.latency_ms}ms</span>
                <span>·</span>
                <span>
                  {run.input_tokens}/{run.output_tokens} tok
                </span>
                <span>·</span>
                <span>${run.cost_usd.toFixed(4)}</span>
                <span>·</span>
                <span>{relativeTime(run.created_at)}</span>
              </div>
              {run.error && (
                <p className="mt-1 text-[10px] text-[var(--color-danger)]">{run.error}</p>
              )}
              {expanded && (
                <pre className="mono animate-fade mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-all rounded-md border border-[var(--color-line-soft)] bg-[#060910] p-2 text-[9.5px] leading-relaxed text-[var(--color-muted)]">
                  {JSON.stringify(run.output, null, 2)}
                </pre>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const CLAIM_STYLE: Record<ClaimStatus, string> = {
  supported: "text-[var(--color-acid)] border-[rgba(200,255,114,0.25)]",
  partial: "text-[var(--color-amber)] border-[rgba(255,196,107,0.3)]",
  unsupported: "text-[var(--color-danger)] border-[rgba(255,111,97,0.3)]",
  contradicted: "text-[var(--color-danger)] border-[rgba(255,111,97,0.4)]",
  unverified: "text-[var(--color-dim)] border-[var(--color-line)]",
};

export function FindingsPanel({ caseFile }: { caseFile: CaseFile }) {
  if (caseFile.findings.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-[var(--color-line)] p-4 text-[11.5px] leading-relaxed text-[var(--color-muted)]">
        Findings appear after the investigator independently challenges its own evidence.
        The critic marks every claim supported, partial, unsupported, or contradicted
        before a plan can be written.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2.5">
      {caseFile.findings.map((finding) => (
        <div
          key={finding.id}
          className="rounded-xl border border-[var(--color-line-soft)] bg-white/[0.012] p-3.5"
        >
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-[12.5px] font-medium leading-snug text-[var(--color-text)]">
                {finding.title}
              </p>
              <p className="mt-1 text-[11px] leading-relaxed text-[var(--color-muted)]">
                {finding.detail}
              </p>
              <p className="mono mt-1.5 text-[9px] text-[var(--color-dim)]">
                {finding.author} · {clockTime(finding.created_at)} ·{" "}
                {percent(finding.confidence)} confidence
              </p>
            </div>
            <ConfidenceRing value={finding.confidence} size={38} />
          </div>

          {finding.claims.length > 0 && (
            <div className="mt-2.5 flex flex-col gap-1.5 border-t border-[var(--color-line-soft)] pt-2.5">
              {finding.claims.map((claim) => (
                <div key={claim.id} className="flex items-start gap-2">
                  <span
                    className={cn(
                      "mt-0.5 shrink-0 rounded border px-1 py-0.5 text-[8.5px] uppercase tracking-wide",
                      CLAIM_STYLE[claim.status],
                    )}
                  >
                    {claim.status}
                  </span>
                  <p className="text-[10.5px] leading-relaxed text-[var(--color-muted)]">
                    {claim.text}
                    <span className="mono ml-1.5 text-[9px] text-[var(--color-dim)]">
                      {claim.evidence_ids.length} evidence
                    </span>
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function NotesPanel({
  caseFile,
  onChanged,
}: {
  caseFile: CaseFile;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const notes = [...caseFile.notes]
    .filter((note) => Boolean(note.body))
    .sort(
      (a, b) =>
        new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime(),
    );

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!body.trim()) return;
    setBusy(true);
    try {
      await api.addNote(caseFile.incident.id, body.trim());
      setBody("");
      toast("Note appended to the ledger.", "good");
      onChanged();
    } catch (error) {
      toast(error instanceof Error ? error.message : "Could not save the note", "bad");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <form onSubmit={submit} className="rounded-xl border border-[var(--color-line)] bg-white/[0.015] p-3">
        <label className="block text-[10px] font-medium uppercase tracking-[0.14em] text-[var(--color-dim)]">
          Operator note
          <textarea
            value={body}
            onChange={(event) => setBody(event.target.value)}
            rows={2}
            maxLength={10000}
            placeholder="What you saw, what you tried, what to watch"
            className="mt-1.5 w-full resize-none rounded-lg border border-[var(--color-line)] bg-[var(--color-ink)] px-2.5 py-2 text-[12px] leading-relaxed text-[var(--color-text)] outline-none transition placeholder:text-[var(--color-dim)]/70 focus:border-[rgba(200,255,114,0.35)]"
          />
        </label>
        <div className="mt-2 flex items-center justify-between">
          <span className="text-[9px] text-[var(--color-dim)]">
            Attributed to the actor in Settings.
          </span>
          <button
            type="submit"
            disabled={busy || body.trim().length === 0}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-[rgba(200,255,114,0.28)] bg-[rgba(200,255,114,0.07)] px-3 text-[11px] font-medium text-[var(--color-acid)] transition hover:bg-[rgba(200,255,114,0.12)] disabled:cursor-not-allowed disabled:opacity-45"
          >
            {busy ? (
              <span className="h-3 w-3 animate-spin-slow rounded-full border border-current border-t-transparent" />
            ) : (
              <IconSend className="h-3 w-3" />
            )}
            Append
          </button>
        </div>
      </form>

      {notes.length === 0 ? (
        <p className="px-1 text-[11px] text-[var(--color-dim)]">
          No operator notes yet. Notes are appended, never edited.
        </p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {notes.map((note) => (
            <div
              key={note.id ?? `${note.created_at}-${note.body?.slice(0, 12)}`}
              className="rounded-lg border border-[var(--color-line-soft)] bg-white/[0.012] px-3 py-2.5"
            >
              <p className="flex items-center gap-2 text-[9px] text-[var(--color-dim)]">
                <IconMail className="h-2.5 w-2.5" />
                <span className="font-medium text-[var(--color-muted)]">
                  {note.author ?? "operator"}
                </span>
                <span>·</span>
                <span>{relativeTime(note.created_at)}</span>
              </p>
              <p className="mt-1 whitespace-pre-wrap text-[11.5px] leading-relaxed text-[var(--color-muted)]">
                {note.body}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
