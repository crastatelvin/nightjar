"use client";

import { useState } from "react";
import { clockTime, cn, shortHash, titleCase } from "@/lib/format";
import type { EvidenceItem } from "@/lib/types";
import {
  IconCloud,
  IconCopy,
  IconDatabase,
  IconKey,
  IconLink,
  IconMail,
  IconPulse,
  IconSearch,
  IconServer,
  IconShield,
  IconSpark,
} from "@/components/icons";
import { useToast } from "@/components/toast";

const UNTRUSTED = new Set(["http_response", "email", "document", "log"]);

function iconFor(kind: string) {
  if (kind === "alert") return IconPulse;
  if (kind === "email") return IconMail;
  if (kind === "log") return IconServer;
  if (kind === "metric") return IconPulse;
  if (kind === "http_response" || kind === "dns" || kind === "tls") return IconCloud;
  if (kind === "config" || kind === "deployment") return IconServer;
  if (kind === "identity_event") return IconShield;
  if (kind === "secret_scan") return IconKey;
  if (kind === "human_note" || kind === "model_note") return IconSpark;
  if (kind === "verification") return IconSearch;
  return IconDatabase;
}

function toneFor(kind: string): string {
  if (kind === "secret_scan") return "text-[var(--color-amber)]";
  if (kind === "http_response") return "text-[var(--color-sky)]";
  if (kind === "email") return "text-[var(--color-violet)]";
  if (kind === "verification") return "text-[var(--color-acid)]";
  return "text-[var(--color-muted)]";
}

export function EvidenceLedger({ evidence }: { evidence: EvidenceItem[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const toast = useToast();
  const ordered = [...evidence].sort(
    (a, b) => new Date(b.observed_at).getTime() - new Date(a.observed_at).getTime(),
  );

  const copy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      toast("Hash copied to clipboard.", "good");
    } catch {
      toast("Clipboard blocked by the browser.", "bad");
    }
  };

  if (ordered.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--color-line)] p-5">
        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-dim)]">
          Empty ledger
        </p>
        <p className="mt-2 text-[12px] leading-relaxed text-[var(--color-muted)]">
          Evidence appears when a read-only probe returns. Every item is redacted, hashed,
          and linked to the previous entry.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {ordered.map((item, index) => {
        const Icon = iconFor(item.kind);
        const expanded = open === item.id;
        const untrusted = UNTRUSTED.has(item.kind);
        return (
          <div
            key={item.id}
            className="group rounded-xl border border-[var(--color-line-soft)] bg-white/[0.012] p-3.5 transition-colors hover:border-[#253246]"
          >
            <div className="flex items-start gap-3">
              <span
                className={cn(
                  "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-[var(--color-line)] bg-[var(--color-ink)]",
                  toneFor(item.kind),
                )}
              >
                <Icon className="h-3.5 w-3.5" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="truncate text-[12.5px] font-medium text-[var(--color-text)]">
                    {item.summary}
                  </p>
                  {untrusted && (
                    <span className="rounded border border-[rgba(255,196,107,0.25)] px-1 py-0.5 text-[8.5px] uppercase tracking-[0.12em] text-[var(--color-amber)]">
                      untrusted data
                    </span>
                  )}
                </div>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[9.5px] text-[var(--color-dim)]">
                  <span className="mono uppercase tracking-wide">{titleCase(item.kind)}</span>
                  <span className="text-[var(--color-line)]">·</span>
                  <span className="mono truncate">{item.source}</span>
                  <span className="text-[var(--color-line)]">·</span>
                  <span>{item.collected_by}</span>
                  <span className="text-[var(--color-line)]">·</span>
                  <span className="mono">{clockTime(item.observed_at)}</span>
                </p>
              </div>
              <button
                type="button"
                onClick={() => setOpen(expanded ? null : item.id)}
                className="shrink-0 rounded-md border border-[var(--color-line)] px-2 py-1 text-[9px] text-[var(--color-dim)] transition hover:border-[#2b3a50] hover:text-[var(--color-muted)]"
              >
                {expanded ? "seal" : "read"}
              </button>
            </div>

            {expanded && (
              <div className="animate-fade mt-2.5 rounded-lg border border-[var(--color-line-soft)] bg-[#060910]">
                <div className="flex items-center justify-between border-b border-[var(--color-line-soft)] px-2.5 py-1.5">
                  <span className="text-[9px] uppercase tracking-[0.14em] text-[var(--color-dim)]">
                    observed content
                  </span>
                  <button
                    type="button"
                    onClick={() => void copy(item.hash)}
                    className="flex items-center gap-1 text-[9px] text-[var(--color-dim)] hover:text-[var(--color-acid)]"
                  >
                    <IconCopy className="h-2.5 w-2.5" /> hash
                  </button>
                </div>
                <pre className="mono max-h-64 overflow-auto whitespace-pre-wrap break-all p-2.5 text-[10px] leading-relaxed text-[var(--color-muted)]">
                  {JSON.stringify(item.content, null, 2)}
                </pre>
              </div>
            )}

            <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-[var(--color-line-soft)] pt-2 text-[9px] text-[var(--color-dim)]">
              <span className="mono" title={item.content_hash}>
                content {shortHash(item.content_hash, 10)}
              </span>
              <span className="flex items-center gap-1">
                <span className="mono" title={item.prev_hash}>
                  {index === ordered.length - 1 ? "genesis" : shortHash(item.prev_hash, 6)}
                </span>
                <IconLink className="h-2.5 w-2.5 text-[var(--color-line)]" />
                <span className="mono text-[var(--color-muted)]" title={item.hash}>
                  {shortHash(item.hash, 8)}
                </span>
              </span>
              <span className="ml-auto mono text-[8.5px] uppercase tracking-wide text-[var(--color-dim)]/70">
                sha-256
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
