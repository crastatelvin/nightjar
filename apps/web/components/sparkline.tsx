import { cn } from "@/lib/format";

export function Sparkline({
  values,
  className,
  tone = "acid",
}: {
  values: number[];
  className?: string;
  tone?: "acid" | "sky" | "danger";
}) {
  const width = 160;
  const height = 40;
  const count = Math.max(2, values.length);
  const max = Math.max(1, ...values);
  const points = values.length
    ? values.map((value, index) => {
        const x = (index / (count - 1)) * width;
        const y = height - 4 - (value / max) * (height - 10);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
    : ["0,36", "160,36"];
  const line = `M ${points.join(" L ")}`;
  const area = `${line} L ${width},${height} L 0,${height} Z`;
  const stroke =
    tone === "danger"
      ? "var(--color-danger)"
      : tone === "sky"
        ? "var(--color-sky)"
        : "var(--color-acid)";

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={cn("h-10 w-40 overflow-visible", className)}
      role="img"
      aria-label="incident volume trend"
    >
      <defs>
        <linearGradient id={`spark-${tone}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#spark-${tone})`} />
      <path
        d={line}
        fill="none"
        stroke={stroke}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        pathLength={1}
        style={{
          strokeDasharray: 1,
          strokeDashoffset: 0,
          animation: "dash 1200ms var(--ease-out-quint) both",
          ["--dash" as string]: 1,
        }}
      />
      {points.length > 0 && (
        <circle
          cx={points[points.length - 1].split(",")[0]}
          cy={points[points.length - 1].split(",")[1]}
          r="2"
          fill={stroke}
        />
      )}
    </svg>
  );
}
