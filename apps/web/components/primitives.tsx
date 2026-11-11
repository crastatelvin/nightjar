import type { ReactNode } from "react";
import { cn } from "@/lib/format";

export function SectionLabel({
  children,
  className,
  action,
}: {
  children: ReactNode;
  className?: string;
  action?: ReactNode;
}) {
  return (
    <div className={cn("flex items-center gap-3", className)}>
      <span className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[var(--color-dim)]">
        {children}
      </span>
      <span className="hairline flex-1" />
      {action}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  body,
  action,
  className,
}: {
  icon: ReactNode;
  title: string;
  body: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-2xl border border-dashed border-[var(--color-line)] px-6 py-12 text-center",
        className,
      )}
    >
      <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-[var(--color-line)] bg-white/[0.02] text-[var(--color-dim)]">
        {icon}
      </span>
      <p className="mt-4 text-[14px] font-medium text-[var(--color-text)]">{title}</p>
      <p className="mt-1.5 max-w-sm text-[12px] leading-relaxed text-[var(--color-muted)]">
        {body}
      </p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("shimmer rounded-lg bg-white/[0.04]", className)} />;
}
