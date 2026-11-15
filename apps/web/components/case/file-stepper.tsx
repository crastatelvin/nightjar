import { cn } from "@/lib/format";
import type { CaseFile } from "@/lib/types";

interface Step {
  id: string;
  label: string;
  detail: string;
}

const STEPS: Step[] = [
  { id: "intake", label: "Intake", detail: "observed" },
  { id: "investigate", label: "Investigate", detail: "evidence" },
  { id: "plan", label: "Plan", detail: "typed actions" },
  { id: "decide", label: "Decide", detail: "policy + human" },
  { id: "execute", label: "Execute", detail: "scoped" },
  { id: "verify", label: "Verify", detail: "T+0 / stability" },
];

function phaseIndex(caseFile: CaseFile): number {
  const status = caseFile.incident.status;
  if (status === "intake") return 0;
  if (status === "investigating") return 1;
  if (status === "awaiting_approval") return 3;
  if (status === "awaiting_manual") return 4;
  if (status === "executing") return 4;
  if (status === "verifying") return 5;
  if (["resolved", "reopened", "failed", "cancelled"].includes(status)) return 6;
  return 1;
}

export function FileStepper({ caseFile }: { caseFile: CaseFile }) {
  const index = phaseIndex(caseFile);
  const failed = caseFile.incident.status === "failed" || caseFile.incident.status === "cancelled";
  const reopened = caseFile.incident.status === "reopened";

  return (
    <div className="panel overflow-hidden px-4 py-4 sm:px-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-[var(--color-dim)]">
          Lifecycle
        </p>
        <span className="mono text-[10px] text-[var(--color-dim)]">
          {failed
            ? "stopped"
            : reopened
              ? "reopened by verification"
              : caseFile.incident.status === "resolved"
                ? "closed by proof"
                : "in flight"}
        </span>
      </div>

      <div className="mt-4 flex items-start overflow-x-auto pb-1 no-scrollbar">
        {STEPS.map((step, stepIndex) => {
          const complete = stepIndex < index;
          const active = stepIndex === index && index < 6;
          const isFailed = failed && active;
          return (
            <div key={step.id} className="flex min-w-[86px] flex-1 flex-col items-center">
              <div className="flex w-full items-center">
                <span
                  className={cn(
                    "mx-auto flex h-7 w-7 items-center justify-center rounded-full border text-[10px] transition-colors",
                    isFailed
                      ? "border-[rgba(255,111,97,0.5)] bg-[rgba(255,111,97,0.1)] text-[var(--color-danger)]"
                      : complete
                        ? "border-[rgba(200,255,114,0.35)] bg-[rgba(200,255,114,0.07)] text-[var(--color-acid)]"
                        : active
                          ? "border-[rgba(124,196,255,0.5)] bg-[rgba(124,196,255,0.08)] text-[var(--color-sky)]"
                          : "border-[var(--color-line)] text-[var(--color-dim)]",
                  )}
                >
                  {complete ? (
                    <svg viewBox="0 0 12 12" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="1.7">
                      <path d="m2.4 6.2 2.3 2.3 4.9-5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  ) : (
                    stepIndex + 1
                  )}
                  {active && !isFailed && (
                    <span className="absolute h-7 w-7 animate-ping rounded-full border border-[var(--color-sky)]/30" />
                  )}
                </span>
              </div>
              <p
                className={cn(
                  "mt-2 text-[10.5px] font-medium",
                  complete || active ? "text-[var(--color-text)]" : "text-[var(--color-dim)]",
                )}
              >
                {step.label}
              </p>
              <p className="mt-0.5 hidden text-[9px] text-[var(--color-dim)] sm:block">
                {step.detail}
              </p>
            </div>
          );
        })}
      </div>

      <div className="mt-3 h-[2px] w-full overflow-hidden rounded-full bg-white/[0.05]">
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-700",
            failed || reopened ? "bg-[var(--color-danger)]" : "bg-[var(--color-acid)]",
          )}
          style={{ width: `${Math.min(100, (index / STEPS.length) * 100)}%` }}
        />
      </div>
    </div>
  );
}
