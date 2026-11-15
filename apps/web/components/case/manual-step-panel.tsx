"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import type { CaseFile, ManualStep } from "@/lib/types";
import { useToast } from "@/components/toast";
import { IconCheck, IconExternal, IconKey, IconShield } from "@/components/icons";

function StepCard({
  step,
  incidentId,
  canAttest,
  onChanged,
}: {
  step: ManualStep;
  incidentId: string;
  canAttest: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const attested = step.state === "attested";
  const proofFailed = attested && canAttest;

  const attest = async () => {
    setBusy(true);
    try {
      await api.attestManualStep(incidentId, step.action_id, note);
      toast("Attested. Nightjar is running the proof now.", "good");
      onChanged();
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : "Attestation failed", "bad");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl border p-3.5 transition-colors",
        attested
          ? "border-[rgba(200,255,114,0.2)] bg-[rgba(200,255,114,0.03)]"
          : "border-[rgba(255,196,107,0.26)] bg-[rgba(255,196,107,0.04)]",
      )}
    >
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-px"
        style={{
          background: attested
            ? "linear-gradient(90deg, transparent, rgba(200,255,114,0.5), transparent)"
            : "linear-gradient(90deg, transparent, rgba(255,196,107,0.55), transparent)",
        }}
      />
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 items-start gap-2">
          <span
            className={cn(
              "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border",
              attested
                ? "border-[rgba(200,255,114,0.24)] text-[var(--color-acid)]"
                : "border-[rgba(255,196,107,0.28)] text-[var(--color-amber)]",
            )}
          >
            {attested ? <IconCheck className="h-3 w-3" /> : <IconKey className="h-3 w-3" />}
          </span>
          <div className="min-w-0">
            <p className="text-[12.5px] font-medium leading-snug text-[var(--color-text)]">
              {step.title || "Manual provider step"}
            </p>
            <p className="mono mt-0.5 text-[9.5px] uppercase tracking-[0.12em] text-[var(--color-dim)]">
              {step.provider} · {step.risk} · {step.action_type}
            </p>
          </div>
        </div>
        <span
          className={cn(
            "mono rounded-full border px-2 py-0.5 text-[9px] uppercase tracking-[0.1em]",
            attested
              ? "border-[rgba(200,255,114,0.24)] text-[var(--color-acid)]"
              : "border-[rgba(255,196,107,0.3)] text-[var(--color-amber)]",
          )}
        >
          {proofFailed ? "proof failed" : attested ? "attested" : "waiting on you"}
        </span>
      </div>

      <ol className="mt-3 space-y-1.5">
        {step.instructions.map((instruction, index) => (
          <li key={instruction} className="flex gap-2 text-[11.5px] leading-relaxed">
            <span className="mono mt-[1px] w-4 shrink-0 text-right text-[9.5px] text-[var(--color-dim)]">
              {index + 1}
            </span>
            <span className="text-[var(--color-muted)]">{instruction}</span>
          </li>
        ))}
      </ol>

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-[var(--color-line-soft)] pt-2.5">
        {step.dashboard_url && (
          <a
            href={step.dashboard_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-[10.5px] text-[var(--color-sky)] underline-grow"
          >
            Open provider console <IconExternal className="h-3 w-3" />
          </a>
        )}
        {step.fingerprint && (
          <span className="mono text-[9.5px] text-[var(--color-dim)]">
            fingerprint {step.fingerprint}
          </span>
        )}
      </div>

      <p className="mt-2 flex items-start gap-1.5 text-[10px] leading-relaxed text-[var(--color-dim)]">
        <IconShield className="mt-[1px] h-3 w-3 shrink-0" />
        {proofFailed
          ? "The credential still worked after your attestation, so the incident reopened. Complete the step for real and attest again."
          : attested
            ? "The proof is running. If the exposed credential still works, the incident reopens and you can attest again."
            : "Nightjar will present the exposed credential and require a rejection. A permission error still means it is live."}
      </p>

      {canAttest && (
        <div className="mt-2.5 flex flex-col gap-2 sm:flex-row">
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Optional: what you did"
            maxLength={2000}
            className="h-8 min-w-0 flex-1 rounded-lg border border-[var(--color-line)] bg-transparent px-2.5 text-[11px] text-[var(--color-text)] outline-none transition placeholder:text-[var(--color-dim)] focus:border-[rgba(255,196,107,0.4)]"
          />
          <button
            type="button"
            onClick={() => void attest()}
            disabled={busy}
            className="inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-lg border border-[rgba(255,196,107,0.34)] bg-[rgba(255,196,107,0.08)] px-3 text-[11px] font-medium text-[var(--color-amber)] transition hover:bg-[rgba(255,196,107,0.14)] disabled:opacity-50"
          >
            {busy ? (
              <span className="h-3 w-3 animate-spin-slow rounded-full border border-current border-t-transparent" />
            ) : (
              <IconCheck className="h-3 w-3" />
            )}
            I completed this step
          </button>
        </div>
      )}
    </div>
  );
}

export function ManualStepPanel({
  caseFile,
  onChanged,
}: {
  caseFile: CaseFile;
  onChanged: () => void;
}) {
  const steps = caseFile.manual_steps ?? [];
  if (steps.length === 0) return null;

  return (
    <div className="flex flex-col gap-2.5">
      {steps.map((step) => (
        <StepCard
          key={step.action_id}
          step={step}
          incidentId={caseFile.incident.id}
          canAttest={
            step.state === "awaiting_manual" || caseFile.incident.status === "reopened"
          }
          onChanged={onChanged}
        />
      ))}
    </div>
  );
}
