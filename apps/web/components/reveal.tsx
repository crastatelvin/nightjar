"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/format";

interface RevealProps {
  children: ReactNode;
  className?: string;
  index?: number;
  as?: "div" | "section" | "li" | "article";
  delay?: number;
}

/** Reveals content once when it enters the viewport. No layout thrash. */
export function Reveal({ children, className, index = 0, as = "div", delay }: RevealProps) {
  const ref = useRef<HTMLElement | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setVisible(true);
            observer.disconnect();
          }
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.08 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const Tag = as;
  return (
    <Tag
      ref={ref as never}
      className={cn("reveal", className)}
      data-visible={visible ? "true" : "false"}
      style={
        {
          "--i": index,
          ...(delay !== undefined ? { transitionDelay: `${delay}ms` } : {}),
        } as React.CSSProperties
      }
    >
      {children}
    </Tag>
  );
}
