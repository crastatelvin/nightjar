"use client";

import { useCountUp } from "@/hooks/use-count-up";
import { cn } from "@/lib/format";

interface CounterProps {
  value: number;
  decimals?: number;
  suffix?: string;
  prefix?: string;
  className?: string;
  duration?: number;
}

export function Counter({
  value,
  decimals = 0,
  suffix = "",
  prefix = "",
  className,
  duration = 900,
}: CounterProps) {
  const animated = useCountUp(value, duration);
  const formatted = animated.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return (
    <span className={cn("tabular-nums", className)}>
      {prefix}
      {formatted}
      {suffix}
    </span>
  );
}
