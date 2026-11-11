import { cn, shortHash } from "@/lib/format";
import type { RiskClass, Severity } from "@/lib/types";
import { IconLink, IconX } from "./icons";

const SEVERITY_STYLE: Record<Severity, string> = {
  critical: "text-[var(--color-danger)] bg-[rgba(255,111,97,0.08)] border-[rgba(255,111,97,0.25)]",
  high: "text-[var(--color-amber)] bg-[rgba(255,196,107,0.07)] border-[rgba(255,196,107,0.22)]",
  medium: "text-[var(--color-sky)] bg-[rgba(124,196,255,0.06)] border-[rgba(124,196,255,0.2)]",
  low: "text-[var(--color-muted)] bg-white/[0.02] border-[var(--color-line)]",
};

export function SeverityChip({ severity }: { severity: Severity }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em]",
        SEVERITY_STYLE[severity],
      )}
    >
      <span className="h-1 w-1 rounded-full bg-current" />
      {severity}
    </span>
  );
}

const RISK_STYLE: Record<RiskClass, string> = {
  r0: "text-[var(--color-dim)] border-[var(--color-line)]",
  r1: "text-[var(--color-mint)] border-[rgba(110,240,194,0.25)]",
  r2: "text-[var(--color-amber)] border-[rgba(255,196,107,0.28)]",
  r3: "text-[var(--color-danger)] border-[rgba(255,111,97,0.35)]",
};

const RISK_LABEL: Record<RiskClass, string> = {
  r0: "read-only",
  r1: "reversible",
  r2: "needs approval",
  r3: "break-glass",
};

export function RiskChip({ risk }: { risk: RiskClass }) {
  return (
    <span
      title={RISK_LABEL[risk]}
      className={cn(
        "mono inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] uppercase",
        RISK_STYLE[risk],
      )}
    >
      {risk}
    </span>
  );
}

export function ChainBadge({
  valid,
  compact = false,
}: {
  valid: boolean;
  compact?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium",
        valid
          ? "border-[rgba(200,255,114,0.22)] bg-[rgba(200,255,114,0.05)] text-[var(--color-acid)]"
          : "border-[rgba(255,111,97,0.3)] bg-[rgba(255,111,97,0.07)] text-[var(--color-danger)]",
      )}
    >
      {valid ? <IconLink className="h-3 w-3" /> : <IconX className="h-3 w-3" />}
      {compact ? (valid ? "sealed" : "broken") : valid ? "chain sealed" : "chain broken"}
    </span>
  );
}

export function Hash({ value, size = 8 }: { value: string; size?: number }) {
  return (
    <span className="mono text-[10px] text-[var(--color-dim)]" title={value}>
      {shortHash(value, size)}
    </span>
  );
}
