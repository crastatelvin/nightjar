import { cn, percent } from "@/lib/format";

export function ConfidenceRing({
  value,
  size = 44,
  stroke = 3,
  className,
}: {
  value: number | null;
  size?: number;
  stroke?: number;
  className?: string;
}) {
  const pct = Math.round((value ?? 0) * 100);
  const radius = (size - stroke) / 2;
  return (
    <div
      className={cn("relative inline-flex items-center justify-center", className)}
      style={{ width: size, height: size }}
      title={value === null ? "no confidence recorded" : `confidence ${percent(value)}`}
    >
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="rgba(255,255,255,0.07)"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={pct >= 70 ? "var(--color-acid)" : pct >= 50 ? "var(--color-amber)" : "var(--color-danger)"}
          strokeWidth={stroke}
          strokeLinecap="round"
          pathLength={100}
          strokeDasharray={`${pct} 100`}
          style={{ transition: "stroke-dasharray 700ms var(--ease-out-quint)" }}
        />
      </svg>
      <span className="mono absolute text-[9px] text-[var(--color-muted)]">{pct}</span>
    </div>
  );
}

export function Meter({
  value,
  tone = "acid",
  className,
}: {
  value: number;
  tone?: "acid" | "amber" | "danger" | "sky";
  className?: string;
}) {
  const color =
    tone === "danger"
      ? "var(--color-danger)"
      : tone === "amber"
        ? "var(--color-amber)"
        : tone === "sky"
          ? "var(--color-sky)"
          : "var(--color-acid)";
  return (
    <div className={cn("h-1 w-full overflow-hidden rounded-full bg-white/[0.06]", className)}>
      <div
        className="h-full rounded-full"
        style={{
          width: `${Math.min(100, Math.max(0, value * 100))}%`,
          background: color,
          boxShadow: `0 0 10px ${color}55`,
          transition: "width 700ms var(--ease-out-quint)",
        }}
      />
    </div>
  );
}
