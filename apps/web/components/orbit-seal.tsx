import { IconCheck, IconShield } from "./icons";
import { cn } from "@/lib/format";

export function OrbitSeal({
  resolved,
  reopened,
  chainValid,
  className,
}: {
  resolved: number;
  reopened: number;
  chainValid: boolean;
  className?: string;
}) {
  return (
    <div className={cn("relative flex flex-col items-center", className)}>
      <div className="relative h-[218px] w-[218px]">
        <div className="absolute inset-0 rounded-full bg-[radial-gradient(circle,rgba(200,255,114,0.09),transparent_62%)]" />

        <svg viewBox="0 0 220 220" className="absolute inset-0 h-full w-full">
          <circle
            cx="110"
            cy="110"
            r="100"
            fill="none"
            stroke="rgba(255,255,255,0.07)"
            strokeWidth="0.8"
          />
          <circle
            cx="110"
            cy="110"
            r="84"
            fill="none"
            stroke="rgba(200,255,114,0.14)"
            strokeWidth="0.8"
            strokeDasharray="2 7"
            style={{ animation: "spin 46s linear infinite", transformOrigin: "110px 110px" }}
          />
          <circle
            cx="110"
            cy="110"
            r="68"
            fill="none"
            stroke={chainValid ? "rgba(200,255,114,0.35)" : "rgba(255,111,97,0.4)"}
            strokeWidth="1"
            pathLength={100}
            strokeDasharray="72 28"
            strokeLinecap="round"
            transform="rotate(-90 110 110)"
            style={{ filter: "drop-shadow(0 0 6px rgba(200,255,114,0.35))" }}
          />
          <g style={{ animation: "spin 18s linear infinite", transformOrigin: "110px 110px" }}>
            <circle cx="110" cy="26" r="2.6" fill="#c8ff72" />
            <circle cx="186" cy="148" r="1.8" fill="#6ef0c2" opacity="0.7" />
          </g>
          <g style={{ animation: "spin 28s linear infinite reverse", transformOrigin: "110px 110px" }}>
            <circle cx="26" cy="128" r="1.6" fill="#7cc4ff" opacity="0.8" />
          </g>
        </svg>

        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span
            className={cn(
              "flex h-16 w-16 items-center justify-center rounded-2xl border",
              chainValid
                ? "border-[rgba(200,255,114,0.28)] bg-[rgba(200,255,114,0.06)] text-[var(--color-acid)] accent-ring"
                : "border-[rgba(255,111,97,0.35)] bg-[rgba(255,111,97,0.07)] text-[var(--color-danger)] danger-ring",
            )}
          >
            {chainValid ? <IconShield className="h-7 w-7" /> : <IconShield className="h-7 w-7" />}
          </span>
          <p className="mono mt-3 text-[26px] font-medium leading-none tracking-tight text-[var(--color-text)]">
            {resolved}
          </p>
          <p className="mt-1 text-[10px] uppercase tracking-[0.2em] text-[var(--color-dim)]">
            proven fixed
          </p>
        </div>

        <span className="absolute left-1/2 top-[7px] -translate-x-1/2 rounded-full border border-[var(--color-line)] bg-[#080c14] px-2 py-0.5 text-[9px] uppercase tracking-[0.14em] text-[var(--color-dim)]">
          T+0
        </span>
        <span className="absolute bottom-[8px] left-1/2 -translate-x-1/2 rounded-full border border-[var(--color-line)] bg-[#080c14] px-2 py-0.5 text-[9px] uppercase tracking-[0.14em] text-[var(--color-dim)]">
          stability
        </span>
        <span className="absolute right-[2px] top-1/2 -translate-y-1/2 rounded-full border border-[var(--color-line)] bg-[#080c14] px-2 py-0.5 text-[9px] uppercase tracking-[0.14em] text-[var(--color-dim)]">
          policy
        </span>
        <span className="absolute left-[2px] top-1/2 -translate-y-1/2 rounded-full border border-[var(--color-line)] bg-[#080c14] px-2 py-0.5 text-[9px] uppercase tracking-[0.14em] text-[var(--color-dim)]">
          chain
        </span>
      </div>

      <div className="mt-3 flex items-center gap-3 text-[10px]">
        <span className="flex items-center gap-1.5 text-[var(--color-acid)]">
          <IconCheck className="h-3 w-3" /> {resolved} verified
        </span>
        <span className="text-[var(--color-line)]">|</span>
        <span
          className={cn(
            "flex items-center gap-1.5",
            reopened > 0 ? "text-[var(--color-danger)]" : "text-[var(--color-dim)]",
          )}
        >
          <span className="h-1 w-1 rounded-full bg-current" /> {reopened} reopened
        </span>
      </div>
    </div>
  );
}
