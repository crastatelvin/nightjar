import { cn, STATUS_LABEL, STATUS_TONE } from "@/lib/format";
import type { IncidentStatus } from "@/lib/types";

const TONE_CLASS: Record<string, string> = {
  live: "text-[var(--color-sky)] border-[rgba(124,196,255,0.22)] bg-[rgba(124,196,255,0.07)]",
  wait: "text-[var(--color-amber)] border-[rgba(255,196,107,0.25)] bg-[rgba(255,196,107,0.07)]",
  good: "text-[var(--color-acid)] border-[rgba(200,255,114,0.24)] bg-[rgba(200,255,114,0.06)]",
  bad: "text-[var(--color-danger)] border-[rgba(255,111,97,0.28)] bg-[rgba(255,111,97,0.07)]",
  muted: "text-[var(--color-dim)] border-[var(--color-line)] bg-white/[0.02]",
};

export function StatusBadge({
  status,
  size = "md",
  className,
}: {
  status: IncidentStatus;
  size?: "sm" | "md";
  className?: string;
}) {
  const tone = STATUS_TONE[status] ?? "muted";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full border font-medium tracking-tight",
        size === "sm" ? "px-2.5 py-0.5 text-[11px]" : "px-3 py-1 text-xs",
        TONE_CLASS[tone],
        className,
      )}
    >
      <span className={cn("status-dot", tone)} />
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

export function StatusPill({ status }: { status: IncidentStatus }) {
  const tone = STATUS_TONE[status] ?? "muted";
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-[var(--color-muted)]">
      <span className={cn("status-dot", tone)} />
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}
