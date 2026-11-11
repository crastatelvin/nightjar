"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { classLabel, cn, relativeTime } from "@/lib/format";
import type { IncidentSummary } from "@/lib/types";
import {
  IconArrow,
  IconCommand,
  IconFlask,
  IconGrid,
  IconPulse,
  IconSearch,
  IconSettings,
} from "./icons";
import { StatusBadge } from "./status-badge";

const ROUTES: Array<{
  href: string;
  label: string;
  hint: string;
  icon: typeof IconGrid;
}> = [
  { href: "/", label: "Command deck", hint: "Overview", icon: IconGrid },
  { href: "/studio", label: "Fault studio", hint: "Simulate", icon: IconFlask },
  { href: "/settings", label: "Settings", hint: "Connection", icon: IconSettings },
];

export function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setCursor(0);
    void api
      .listIncidents(30)
      .then((data) => setIncidents(data.items))
      .catch(() => setIncidents([]));
    const timer = window.setTimeout(() => inputRef.current?.focus(), 40);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  const results = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const routes = ROUTES.filter(
      (route) =>
        !needle ||
        route.label.toLowerCase().includes(needle) ||
        route.hint.toLowerCase().includes(needle),
    );
    const matched = incidents
      .filter(
        (incident) =>
          !needle ||
          incident.title.toLowerCase().includes(needle) ||
          incident.id.toLowerCase().includes(needle) ||
          incident.status.includes(needle),
      )
      .slice(0, 6);
    return { routes, incidents: matched };
  }, [query, incidents]);

  const total = results.routes.length + results.incidents.length;

  useEffect(() => {
    setCursor((value) => Math.min(value, Math.max(0, total - 1)));
  }, [total]);

  if (!open) return null;

  const go = (href: string) => {
    onClose();
    router.push(href);
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setCursor((value) => (total ? (value + 1) % total : 0));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setCursor((value) => (total ? (value - 1 + total) % total : 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const route = results.routes[cursor];
      if (route) return go(route.href);
      const incident = results.incidents[cursor - results.routes.length];
      if (incident) go(`/incidents/${incident.id}`);
    }
  };

  let index = -1;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-start justify-center px-4 pt-[12vh]"
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <button
        type="button"
        className="absolute inset-0 cursor-default bg-[#04060a]/70 backdrop-blur-sm animate-fade"
        aria-label="Close"
        onClick={onClose}
      />
      <div className="animate-rise panel relative w-full max-w-[560px] overflow-hidden shadow-[0_40px_120px_-30px_rgba(0,0,0,0.9)]">
        <div className="flex items-center gap-3 border-b border-[var(--color-line)] px-4 py-3.5">
          <IconSearch className="h-4 w-4 text-[var(--color-dim)]" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search incidents, jump to a page…"
            className="h-6 flex-1 bg-transparent text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-dim)]"
          />
          <span className="kbd">esc</span>
        </div>

        <div className="max-h-[52vh] overflow-y-auto p-2">
          {results.routes.length > 0 && (
            <p className="px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-[var(--color-dim)]">
              Navigate
            </p>
          )}
          {results.routes.map((route) => {
            index += 1;
            const active = index === cursor;
            const Icon = route.icon;
            return (
              <button
                key={route.href}
                type="button"
                onMouseEnter={() => setCursor(index)}
                onClick={() => go(route.href)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-lg px-2.5 py-2.5 text-left transition",
                  active ? "bg-white/[0.055]" : "hover:bg-white/[0.03]",
                )}
              >
                <span className="flex h-7 w-7 items-center justify-center rounded-md border border-[var(--color-line)] bg-[var(--color-ink)] text-[var(--color-muted)]">
                  <Icon className="h-3.5 w-3.5" />
                </span>
                <span className="flex-1 text-[13px] font-medium">{route.label}</span>
                <span className="text-[11px] text-[var(--color-dim)]">{route.hint}</span>
                {active && <IconArrow className="h-3.5 w-3.5 text-[var(--color-acid)]" />}
              </button>
            );
          })}

          {results.incidents.length > 0 && (
            <p className="px-2 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-[var(--color-dim)]">
              Incidents
            </p>
          )}
          {results.incidents.map((incident) => {
            index += 1;
            const active = index === cursor;
            return (
              <button
                key={incident.id}
                type="button"
                onMouseEnter={() => setCursor(index)}
                onClick={() => go(`/incidents/${incident.id}`)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-lg px-2.5 py-2.5 text-left transition",
                  active ? "bg-white/[0.055]" : "hover:bg-white/[0.03]",
                )}
              >
                <span className="flex h-7 w-7 items-center justify-center rounded-md border border-[var(--color-line)] bg-[var(--color-ink)] text-[var(--color-muted)]">
                  <IconPulse className="h-3.5 w-3.5" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium">{incident.title}</span>
                  <span className="block text-[11px] text-[var(--color-dim)]">
                    {classLabel(incident.incident_class)} · {relativeTime(incident.created_at)}
                  </span>
                </span>
                <StatusBadge status={incident.status} size="sm" />
              </button>
            );
          })}

          {total === 0 && (
            <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
              <IconCommand className="h-5 w-5 text-[var(--color-dim)]" />
              <p className="text-[13px] text-[var(--color-muted)]">Nothing matches “{query}”.</p>
              <p className="text-[11px] text-[var(--color-dim)]">
                Try an incident title, status, or page name.
              </p>
            </div>
          )}
        </div>

        <div className="flex items-center gap-4 border-t border-[var(--color-line)] px-4 py-2.5 text-[10px] text-[var(--color-dim)]">
          <span className="flex items-center gap-1.5">
            <span className="kbd">↑</span>
            <span className="kbd">↓</span> navigate
          </span>
          <span className="flex items-center gap-1.5">
            <span className="kbd">↵</span> open
          </span>
          <span className="ml-auto mono">{total} results</span>
        </div>
      </div>
    </div>
  );
}
