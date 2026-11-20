"use client";

import { useState } from "react";
import { clockTime, cn, shortHash, titleCase } from "@/lib/format";
import type { TimelineEvent } from "@/lib/types";
import {
  IconAlert,
  IconCheck,
  IconDatabase,
  IconKey,
  IconLink,
  IconLock,
  IconPen,
  IconPulse,
  IconRadar,
  IconSearch,
  IconServer,
  IconShield,
  IconSpark,
  IconX,
  IconZap,
} from "@/components/icons";

function toneFor(type: string): "good" | "bad" | "info" | "wait" {
  if (type === "incident.resolved") return "good";
  if (type === "action.replayed") return "good";
  if (type === "incident.reopened" || type === "action.failed") return "bad";
  // The mutation may have landed; it is not a failure and not good news either.
  if (type === "action.outcome_unknown") return "wait";
  if (type.startsWith("approval") || type === "policy.decided") return "wait";
  return "info";
}

function iconFor(type: string) {
  if (type === "incident.created") return IconSpark;
  if (type.startsWith("evidence")) return IconDatabase;
  if (type.startsWith("triage")) return IconRadar;
  if (type.startsWith("finding") || type.startsWith("critique")) return IconSearch;
  if (type.startsWith("plan")) return IconZap;
  if (type === "policy.decided") return IconLock;
  if (type.startsWith("approval")) return IconShield;
  if (type === "action.replayed") return IconLink;
  if (type === "action.outcome_unknown") return IconAlert;
  if (type.startsWith("action")) return IconServer;
  if (type.startsWith("verification")) return IconCheck;
  if (type === "incident.reopened") return IconAlert;
  if (type === "incident.resolved") return IconCheck;
  if (type === "note") return IconPen;
  return IconPulse;
}

const TONE_CLASS = {
  good: "text-[var(--color-acid)] border-[rgba(200,255,114,0.3)] bg-[rgba(200,255,114,0.06)]",
  bad: "text-[var(--color-danger)] border-[rgba(255,111,97,0.32)] bg-[rgba(255,111,97,0.07)]",
  wait: "text-[var(--color-amber)] border-[rgba(255,196,107,0.28)] bg-[rgba(255,196,107,0.06)]",
  info: "text-[var(--color-sky)] border-[rgba(124,196,255,0.24)] bg-[rgba(124,196,255,0.05)]",
};

function summaryOf(event: TimelineEvent): string {
  const payload = event.payload;
  // The note body lives in the Operator notes panel; repeating it here would
  // duplicate the same paragraph twice on one screen. The ledger still records
  // it in the payload, one click away.
  if (event.event_type === "note") return "Operator note added";
  const status = payload.status ?? payload.to;
  if (typeof status === "string" && event.event_type === "incident.status_changed") {
    return `Status → ${titleCase(status)}`;
  }
  const keys = Object.keys(payload);
  if (keys.length === 0) return titleCase(event.event_type);
  const preferred = ["summary", "message", "title", "reason", "decision", "action_type", "passed"];
  for (const key of preferred) {
    if (key in payload) {
      const value = payload[key];
      if (typeof value === "boolean") return `${titleCase(key)}: ${value ? "yes" : "no"}`;
      if (typeof value === "string" || typeof value === "number") {
        return `${titleCase(key)}: ${String(value)}`;
      }
    }
  }
  return keys
    .slice(0, 2)
    .map((key) => `${titleCase(key)}: ${String(payload[key]).slice(0, 42)}`)
    .join(" · ");
}

export function Timeline({ events }: { events: TimelineEvent[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const ordered = [...events].sort((a, b) => b.seq - a.seq);

  return (
    <div className="relative">
      <div className="absolute bottom-4 left-[15px] top-4 w-px bg-gradient-to-b from-[rgba(200,255,114,0.25)] via-[var(--color-line)] to-transparent" />
      <ol className="flex flex-col">
        {ordered.map((event, index) => {
          const tone = toneFor(event.event_type);
          const Icon = iconFor(event.event_type);
          const open = expanded === event.id;
          return (
            <li key={event.id} className="relative pl-10">
              <span
                className={cn(
                  "absolute left-0 top-2.5 flex h-[31px] w-[31px] items-center justify-center rounded-full border",
                  TONE_CLASS[tone],
                )}
              >
                <Icon className="h-3.5 w-3.5" />
                {index === 0 && (
                  <span className="absolute inset-0 animate-ping rounded-full border border-current opacity-20" />
                )}
              </span>
              <div className="group border-b border-[var(--color-line-soft)] py-2.5 pr-1 last:border-0">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px]">
                      <span className="font-medium text-[var(--color-text)]">
                        {titleCase(event.event_type)}
                      </span>
                      <span className="text-[10px] text-[var(--color-dim)]">
                        {summaryOf(event)}
                      </span>
                    </p>
                    <p className="mt-1 flex items-center gap-2 text-[9.5px] text-[var(--color-dim)]">
                      <span className="mono">{clockTime(event.created_at)}</span>
                      <span className="text-[var(--color-line)]">·</span>
                      <span className="inline-flex items-center gap-1">
                        <IconKey className="h-2.5 w-2.5" />
                        {event.actor}
                      </span>
                      <span className="text-[var(--color-line)]">·</span>
                      <span className="mono" title={event.hash}>
                        {shortHash(event.hash, 10)}
                      </span>
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setExpanded(open ? null : event.id)}
                    className={cn(
                      "shrink-0 rounded border border-[var(--color-line)] px-1.5 py-0.5 text-[9px] text-[var(--color-dim)] transition hover:border-[#2b3a50] hover:text-[var(--color-muted)]",
                      open && "border-[rgba(200,255,114,0.25)] text-[var(--color-acid)]",
                    )}
                  >
                    {open ? <IconX className="h-2.5 w-2.5" /> : <IconLink className="h-2.5 w-2.5" />}
                  </button>
                </div>

                {open && (
                  <div className="animate-fade mt-2 rounded-lg border border-[var(--color-line-soft)] bg-[#060910] p-2.5">
                    <div className="mb-1.5 flex items-center justify-between text-[9px] text-[var(--color-dim)]">
                      <span className="mono">
                        seq {event.seq} · prev {shortHash(event.prev_hash, 8)}
                      </span>
                      <span className="mono">hash {shortHash(event.hash, 12)}</span>
                    </div>
                    <pre className="mono max-h-60 overflow-auto whitespace-pre-wrap break-all text-[10px] leading-relaxed text-[var(--color-muted)]">
                      {JSON.stringify(event.payload, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {ordered.length === 0 && (
        <p className="pl-10 text-[12px] text-[var(--color-dim)]">
          No events yet. The ledger writes as the incident unfolds.
        </p>
      )}
    </div>
  );
}
