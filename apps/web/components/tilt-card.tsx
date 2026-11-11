"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/format";
import { useTilt } from "@/hooks/use-tilt";

interface TiltCardProps {
  children: ReactNode;
  className?: string;
  scale?: number;
  tilt?: number;
}

export function TiltCard({ children, className, scale = 1.008, tilt = 5 }: TiltCardProps) {
  const { ref, onPointerMove, onPointerLeave } = useTilt<HTMLDivElement>({ scale, max: tilt });
  return (
    <div
      ref={ref}
      onPointerMove={onPointerMove}
      onPointerLeave={onPointerLeave}
      className={cn("tilt", className)}
    >
      {children}
    </div>
  );
}
