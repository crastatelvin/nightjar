"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { cn } from "@/lib/format";
import { IconAlert, IconCheck, IconX } from "./icons";

type Tone = "good" | "bad" | "info";

interface ToastItem {
  id: number;
  message: string;
  tone: Tone;
}

const ToastContext = createContext<(message: string, tone?: Tone) => void>(() => {});

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const next = useRef(1);

  const push = useCallback((message: string, tone: Tone = "info") => {
    const id = next.current++;
    setItems((current) => [...current.slice(-2), { id, message, tone }]);
    window.setTimeout(() => {
      setItems((current) => current.filter((item) => item.id !== id));
    }, 4200);
  }, []);

  const value = useMemo(() => push, [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[90] flex w-[min(92vw,360px)] flex-col gap-2">
        {items.map((item) => (
          <div
            key={item.id}
            role="status"
            className={cn(
              "animate-rise glass pointer-events-auto flex items-start gap-3 rounded-xl border px-3.5 py-3 text-[13px] shadow-2xl",
              item.tone === "good" &&
                "border-[rgba(200,255,114,0.28)] text-[var(--color-acid)]",
              item.tone === "bad" &&
                "border-[rgba(255,111,97,0.32)] text-[var(--color-danger)]",
              item.tone === "info" && "border-[var(--color-line)] text-[var(--color-text)]",
            )}
          >
            <span className="mt-0.5">
              {item.tone === "bad" ? (
                <IconAlert className="h-4 w-4" />
              ) : item.tone === "good" ? (
                <IconCheck className="h-4 w-4" />
              ) : (
                <span className="block h-1.5 w-1.5 rounded-full bg-[var(--color-sky)]" />
              )}
            </span>
            <span className="flex-1 leading-snug">{item.message}</span>
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => setItems((current) => current.filter((i) => i.id !== item.id))}
              className="text-[var(--color-dim)] transition hover:text-[var(--color-text)]"
            >
              <IconX className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
