import { cn } from "@/lib/format";

export function Logo({ className, size = 30 }: { className?: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      className={cn(className)}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="nj-mark" x1="4" y1="4" x2="36" y2="36">
          <stop stopColor="#c8ff72" />
          <stop offset="0.55" stopColor="#6ef0c2" />
          <stop offset="1" stopColor="#7cc4ff" />
        </linearGradient>
      </defs>
      <path
        d="M20 3.5 34 11v18L20 36.5 6 29V11L20 3.5Z"
        stroke="url(#nj-mark)"
        strokeWidth="1.1"
        opacity="0.5"
      />
      <path
        d="M13 25.5c2.6 1.7 6 2.4 8.8 1.1 3.6-1.7 5-6 2.5-9.2-1.8-2.3-4.8-2.7-6.8-1.4 2.2.3 3.7 1.7 3.9 3.7.2 2.4-2.1 3.9-4.6 3.6-1.3-.2-2.6-.8-3.8-1.8-.7 1.3-.7 2.8 0 4Z"
        fill="url(#nj-mark)"
      />
      <circle cx="27.5" cy="12.5" r="1.4" fill="#c8ff72" />
    </svg>
  );
}

export function Wordmark({ compact = false }: { compact?: boolean }) {
  return (
    <span className="flex items-center gap-2.5">
      <Logo size={compact ? 24 : 28} />
      {!compact && (
        <span className="text-[15px] font-semibold tracking-[-0.02em] text-[var(--color-text)]">
          nightjar
        </span>
      )}
    </span>
  );
}
