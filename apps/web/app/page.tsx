"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  classLabel,
  cn,
  isActive,
  relativeTime,
  titleCase,
} from "@/lib/format";
import type { CaseFile, IncidentList, IncidentSummary, IncidentStatus } from "@/lib/types";
import { ApprovalQueue, IncidentRow, NewIncidentButton } from "@/components/dashboard/approval-queue";
import { Counter } from "@/components/counter";
import { ConfidenceRing, Meter } from "@/components/data-viz";
import {
  IconAlert,
  IconArrow,
  IconCheck,
  IconCloud,
  IconDatabase,
  IconFlask,
  IconLink,
  IconPulse,
  IconRefresh,
  IconSearch,
  IconServer,
  IconShield,
  IconZap,
} from "@/components/icons";
import { OrbitSeal } from "@/components/orbit-seal";
import { EmptyState, SectionLabel, Skeleton } from "@/components/primitives";
import { Reveal } from "@/components/reveal";
import { Sparkline } from "@/components/sparkline";
import { StatusBadge, StatusPill } from "@/components/status-badge";
import { useSystem } from "@/components/system-context";
import { TiltCard } from "@/components/tilt-card";
import { useToast } from "@/components/toast";

type Filter = "all" | "active" | "awaiting" | "closed" | "reopened";

const FILTERS: Array<{ id: Filter; label: string }> = [
  { id: "all", label: "All" },
  { id: "active", label: "Active" },
  { id: "awaiting", label: "Awaiting" },
  { id: "closed", label: "Closed" },
  { id: "reopened", label: "Reopened" },
];

function matchesFilter(status: IncidentStatus, filter: Filter): boolean {
  if (filter === "all") return true;
  if (filter === "active") return isActive(status);
  if (filter === "awaiting") return ["awaiting_approval", "awaiting_manual"].includes(status);
  if (filter === "closed") return ["resolved", "failed", "cancelled"].includes(status);
  return status === "reopened";
}

function buckets(items: IncidentSummary[], hours = 24, count = 12): number[] {
  const now = Date.now();
  const span = (hours * 3600 * 1000) / count;
  const values = Array.from({ length: count }, () => 0);
  for (const item of items) {
    const age = now - new Date(item.created_at).getTime();
    if (age < 0 || age > hours * 3600 * 1000) continue;
    const index = count - 1 - Math.floor(age / span);
    if (index >= 0 && index < count) values[index] += 1;
  }
  return values;
}

export default function DashboardPage() {
  const { meta, online } = useSystem();
  const toast = useToast();
  const [data, setData] = useState<IncidentList | null>(null);
  const [awaitingCases, setAwaitingCases] = useState<CaseFile[]>([]);
  const [proofCase, setProofCase] = useState<CaseFile | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [failed, setFailed] = useState(false);
  const signature = useRef("");

  const load = useCallback(async (announce = false) => {
    setSyncing(true);
    try {
      const list = await api.listIncidents(100);
      setData(list);
      setFailed(false);

      const awaiting = list.items
        .filter((item) => ["awaiting_approval", "awaiting_manual"].includes(item.status))
        .slice(0, 3);
      const resolved = list.items.find((item) => item.status === "resolved");
      const nextSignature = [
        ...awaiting.map((item) => `${item.id}:${item.updated_at}`),
        resolved ? `${resolved.id}:${resolved.updated_at}` : "none",
      ].join("|");

      if (nextSignature !== signature.current) {
        signature.current = nextSignature;
        const details = await Promise.allSettled(
          [...awaiting, ...(resolved ? [resolved] : [])].map((item) =>
            api.getIncident(item.id),
          ),
        );
        const files = details
          .filter((result): result is PromiseFulfilledResult<CaseFile> => result.status === "fulfilled")
          .map((result) => result.value);
        setAwaitingCases(
          files.filter((file) => file.incident.status === "awaiting_approval"),
        );
        setProofCase(files.find((file) => file.incident.status === "resolved") ?? null);
      }
      if (announce) toast("Control plane synced.", "good");
    } catch (error) {
      setFailed(true);
      if (announce) {
        toast(error instanceof Error ? error.message : "Sync failed", "bad");
      }
    } finally {
      setSyncing(false);
    }
  }, [toast]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  const items = data?.items ?? [];
  const stats = useMemo(() => {
    const active = items.filter((item) => isActive(item.status));
    const awaiting = items.filter((item) =>
      ["awaiting_approval", "awaiting_manual"].includes(item.status),
    );
    const resolved24 = items.filter(
      (item) =>
        item.status === "resolved" &&
        item.resolved_at !== null &&
        Date.now() - new Date(item.resolved_at).getTime() < 24 * 3600 * 1000,
    );
    const reopened = items.filter((item) => item.status === "reopened");
    return { active, awaiting, resolved24, reopened };
  }, [items]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return items.filter((item) => {
      if (!matchesFilter(item.status, filter)) return false;
      if (!needle) return true;
      return (
        item.title.toLowerCase().includes(needle) ||
        item.id.toLowerCase().includes(needle) ||
        item.incident_class.includes(needle)
      );
    });
  }, [items, filter, query]);

  const counts = useMemo(
    () => ({
      all: items.length,
      active: stats.active.length,
      awaiting: stats.awaiting.length,
      closed: items.filter((item) => ["resolved", "failed", "cancelled"].includes(item.status))
        .length,
      reopened: stats.reopened.length,
    }),
    [items, stats],
  );

  const trend = useMemo(() => buckets(items), [items]);
  const classes = useMemo(() => {
    const map = new Map<string, number>();
    for (const item of items) {
      map.set(item.incident_class, (map.get(item.incident_class) ?? 0) + 1);
    }
    return [...map.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4);
  }, [items]);

  const chainValid = proofCase ? proofCase.chain.evidence_valid && proofCase.chain.timeline_valid : online;
  const falseResolutionRisk = proofCase
    ? proofCase.incident.status === "resolved" &&
      !(proofCase.chain.evidence_valid && proofCase.chain.timeline_valid)
    : false;

  return (
    <div className="mx-auto max-w-[1480px] px-4 pb-10 pt-6 sm:px-6 lg:px-8 lg:pt-9">
      {falseResolutionRisk && (
        <div className="danger-ring mb-5 flex items-start gap-3 rounded-xl border border-[rgba(255,111,97,0.35)] bg-[rgba(255,111,97,0.08)] p-4">
          <IconAlert className="mt-0.5 h-4 w-4 text-[var(--color-danger)]" />
          <div>
            <p className="text-[13px] font-semibold text-[var(--color-danger)]">
              Unverified resolution detected
            </p>
            <p className="mt-1 text-[12px] text-[var(--color-muted)]">
              An incident is marked resolved but its evidence chain does not verify. Treat the
              claim as false until proven.
            </p>
          </div>
        </div>
      )}

      <section className="relative overflow-hidden rounded-3xl border border-[var(--color-line)] bg-[linear-gradient(180deg,rgba(16,23,37,0.7),rgba(7,10,16,0.4))] px-5 py-7 sm:px-8 sm:py-9">
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.28]"
          style={{
            backgroundImage:
              "linear-gradient(rgba(255,255,255,0.045) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.045) 1px, transparent 1px)",
            backgroundSize: "46px 46px",
            maskImage: "radial-gradient(ellipse at 70% 20%, #000 5%, transparent 72%)",
            WebkitMaskImage: "radial-gradient(ellipse at 70% 20%, #000 5%, transparent 72%)",
          }}
        />
        <div className="relative grid items-center gap-8 lg:grid-cols-[1.35fr_0.65fr]">
          <div>
            <div className="flex flex-wrap items-center gap-2.5">
              <span className="inline-flex items-center gap-2 rounded-full border border-[rgba(200,255,114,0.22)] bg-[rgba(200,255,114,0.05)] px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--color-acid)]">
                <span className="status-dot live" />
                verification-first
              </span>
              <span className="mono text-[10px] text-[var(--color-dim)]">
                {online ? "control plane live" : "control plane offline"}
              </span>
            </div>
            <h1 className="mt-5 max-w-[20ch] text-balance text-[clamp(2rem,5vw,3.35rem)] font-semibold leading-[1.02] tracking-[-0.045em]">
              Nothing is resolved
              <br />
              until it is{" "}
              <span className="bg-[linear-gradient(100deg,#c8ff72,#6ef0c2_45%,#7cc4ff)] bg-clip-text text-transparent">
                proven.
              </span>
            </h1>
            <p className="mt-4 max-w-[54ch] text-[13.5px] leading-relaxed text-[var(--color-muted)] sm:text-[14px]">
              Nightjar investigates with evidence, asks before it changes anything, then
              re-checks the world at T+0 and after the stability window. A green badge is a
              claim with a hash behind it, not a model's opinion.
            </p>
            <div className="mt-6 flex flex-wrap items-center gap-2.5">
              <Link
                href="/studio"
                className="group inline-flex h-10 items-center gap-2 rounded-lg border border-[rgba(200,255,114,0.32)] bg-[rgba(200,255,114,0.09)] px-4 text-[12.5px] font-semibold text-[var(--color-acid)] transition hover:bg-[rgba(200,255,114,0.15)]"
              >
                <IconFlask className="h-3.5 w-3.5" />
                Run a fault
                <IconArrow className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
              </Link>
              <NewIncidentButton />
              <button
                type="button"
                onClick={() => void load(true)}
                className="inline-flex h-10 items-center gap-2 rounded-lg border border-[var(--color-line)] px-3.5 text-[12px] text-[var(--color-muted)] transition hover:border-[#2b3a50] hover:text-[var(--color-text)]"
              >
                <IconRefresh className={cn("h-3.5 w-3.5", syncing && "animate-spin-slow")} />
                Sync
              </button>
            </div>
            <div className="mt-7 flex flex-wrap items-center gap-x-5 gap-y-2 text-[10.5px] text-[var(--color-dim)]">
              <span className="flex items-center gap-1.5">
                <IconShield className="h-3 w-3 text-[var(--color-mint)]" /> typed actions only
              </span>
              <span className="flex items-center gap-1.5">
                <IconDatabase className="h-3 w-3 text-[var(--color-sky)]" /> hash-chained ledger
              </span>
              <span className="flex items-center gap-1.5">
                <IconCheck className="h-3 w-3 text-[var(--color-acid)]" /> T+0 and stability proof
              </span>
            </div>
          </div>

          <div className="relative flex justify-center lg:justify-end">
            <OrbitSeal
              resolved={stats.resolved24.length}
              reopened={stats.reopened.length}
              chainValid={chainValid}
            />
          </div>
        </div>
      </section>

      <section className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Reveal index={0}>
          <Kpi
            label="Open now"
            value={stats.active.length}
            caption={`${counts.awaiting} awaiting a human`}
            icon={<IconPulse className="h-3.5 w-3.5" />}
            href="#incidents"
            spark={trend}
            tone="sky"
          />
        </Reveal>
        <Reveal index={1}>
          <Kpi
            label="Decisions"
            value={stats.awaiting.length}
            caption={stats.awaiting.length ? "policy stopped the machine" : "queue clear"}
            icon={<IconShield className="h-3.5 w-3.5" />}
            href="#approvals"
            spark={trend}
            tone="amber"
          />
        </Reveal>
        <Reveal index={2}>
          <Kpi
            label="Proven in 24h"
            value={stats.resolved24.length}
            caption="verified, not assumed"
            icon={<IconCheck className="h-3.5 w-3.5" />}
            href="#incidents"
            spark={trend}
            tone="acid"
          />
        </Reveal>
        <Reveal index={3}>
          <Kpi
            label="Reopened"
            value={stats.reopened.length}
            caption="stability check caught relapse"
            icon={<IconAlert className="h-3.5 w-3.5" />}
            href="#incidents"
            spark={trend}
            tone={stats.reopened.length ? "danger" : "muted"}
          />
        </Reveal>
      </section>

      <div className="mt-6 grid gap-5 xl:grid-cols-[1.6fr_0.8fr]">
        <div className="min-w-0">
          <section id="incidents" className="panel scroll-mt-24 overflow-hidden">
            <div className="flex flex-col gap-4 border-b border-[var(--color-line)] p-4 sm:p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <SectionLabel className="w-full sm:w-auto">Incident stream</SectionLabel>
                  <p className="mt-2 text-[12px] text-[var(--color-muted)]">
                    Every row is a case file. Open one to read the evidence.
                  </p>
                </div>
                <span className="mono text-[10px] text-[var(--color-dim)]">
                  {data ? `${data.total} total` : "connecting…"}
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <div className="flex flex-wrap items-center gap-1">
                  {FILTERS.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setFilter(item.id)}
                      aria-pressed={filter === item.id}
                      className={cn(
                        "flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[11px] transition",
                        filter === item.id
                          ? "bg-white/[0.07] text-[var(--color-text)]"
                          : "text-[var(--color-dim)] hover:bg-white/[0.03] hover:text-[var(--color-muted)]",
                      )}
                    >
                      {item.label}
                      <span
                        className={cn(
                          "mono text-[9px]",
                          filter === item.id
                            ? "text-[var(--color-acid)]"
                            : "text-[var(--color-dim)]",
                        )}
                      >
                        {counts[item.id]}
                      </span>
                    </button>
                  ))}
                </div>
                <div className="relative ml-auto">
                  <IconSearch className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-dim)]" />
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Filter"
                    aria-label="Filter incidents"
                    className="h-8 w-32 rounded-md border border-[var(--color-line)] bg-[var(--color-ink)] pl-8 pr-2 text-[11px] text-[var(--color-text)] outline-none transition focus:w-44 focus:border-[rgba(200,255,114,0.35)]"
                  />
                </div>
              </div>
            </div>

            {!data && failed ? (
              <div className="p-5">
                <EmptyState
                  icon={<IconAlert className="h-4 w-4" />}
                  title="The API is not answering"
                  body="Start the control plane or update the API address in Settings. The UI will reconnect automatically."
                  action={
                    <Link
                      href="/settings"
                      className="inline-flex h-9 items-center gap-2 rounded-lg border border-[var(--color-line)] px-3 text-[12px] text-[var(--color-muted)]"
                    >
                      Open connection settings <IconArrow className="h-3 w-3" />
                    </Link>
                  }
                />
              </div>
            ) : !data ? (
              <div className="space-y-3 p-5">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : filtered.length === 0 ? (
              <div className="p-5">
                <EmptyState
                  icon={<IconSearch className="h-4 w-4" />}
                  title={items.length === 0 ? "No incidents yet" : "Nothing matches this view"}
                  body={
                    items.length === 0
                      ? "Inject a fault in the studio or file an incident. The case file will appear here the moment intake begins."
                      : "Try another filter, or clear the search. The ledger keeps everything."
                  }
                  action={
                    items.length === 0 ? (
                      <Link
                        href="/studio"
                        className="inline-flex h-9 items-center gap-2 rounded-lg border border-[rgba(200,255,114,0.3)] bg-[rgba(200,255,114,0.07)] px-3 text-[12px] font-medium text-[var(--color-acid)]"
                      >
                        <IconFlask className="h-3.5 w-3.5" /> Open the studio
                      </Link>
                    ) : (
                      <button
                        type="button"
                        onClick={() => {
                          setFilter("all");
                          setQuery("");
                        }}
                        className="inline-flex h-9 items-center rounded-lg border border-[var(--color-line)] px-3 text-[12px] text-[var(--color-muted)]"
                      >
                        Clear filters
                      </button>
                    )
                  }
                />
              </div>
            ) : (
              <div className="max-h-[620px] overflow-y-auto">
                {filtered.map((incident, index) => (
                  <IncidentRow key={incident.id} incident={incident} index={index} />
                ))}
              </div>
            )}

            <div className="flex items-center justify-between border-t border-[var(--color-line)] px-4 py-3 text-[10px] text-[var(--color-dim)]">
              <span className="flex items-center gap-2">
                <span
                  className={cn(
                    "status-dot",
                    online ? "text-[var(--color-acid)] live" : "text-[var(--color-danger)]",
                  )}
                />
                {syncing ? "syncing ledger" : "live · refreshes every 5s"}
              </span>
              <span className="mono">{filtered.length} shown</span>
            </div>
          </section>
        </div>

        <aside className="flex min-w-0 flex-col gap-5">
          <section id="approvals" className="scroll-mt-24">
            <div className="mb-2.5 flex items-center justify-between">
              <SectionLabel className="flex-1">Human decisions</SectionLabel>
              <span className="mono ml-3 text-[10px] text-[var(--color-dim)]">
                {stats.awaiting.length} pending
              </span>
            </div>
            <ApprovalQueue
              cases={awaitingCases}
              loading={!data}
              onChanged={() => void load()}
            />
          </section>

          <section className="panel p-4">
            <SectionLabel>Control plane</SectionLabel>
            <div className="mt-3 flex flex-col gap-0.5">
              <Row
                label="API"
                value={online ? "connected" : "offline"}
                good={online}
                icon={<IconPulse className="h-3 w-3" />}
              />
              <Row
                label="Model"
                value={meta ? `${meta.provider}` : "unknown"}
                icon={<IconZap className="h-3 w-3" />}
              />
              <Row
                label="Policy"
                value="deterministic"
                good
                icon={<IconShield className="h-3 w-3" />}
              />
              <Row
                label="Ledger"
                value={chainValid ? "sealed" : "unverified"}
                good={chainValid}
                icon={<IconLink className="h-3 w-3" />}
              />
              <Row
                label="Actions"
                value={meta ? `${Object.keys(meta.action_catalog).length} typed` : "—"}
                icon={<IconServer className="h-3 w-3" />}
              />
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {(meta?.connectors ?? []).map((connector) => (
                <span
                  key={connector}
                  className="mono inline-flex items-center gap-1 rounded border border-[var(--color-line)] bg-white/[0.02] px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-[var(--color-dim)]"
                >
                  <IconCloud className="h-2.5 w-2.5" /> {connector}
                </span>
              ))}
            </div>
          </section>

          <section className="panel overflow-hidden">
            <div className="flex items-center justify-between p-4 pb-2">
              <SectionLabel className="flex-1">Latest proof</SectionLabel>
              <span className="mono ml-3 text-[10px] text-[var(--color-dim)]">T+0</span>
            </div>
            {proofCase ? (
              <div className="px-4 pb-4">
                <Link
                  href={`/incidents/${proofCase.incident.id}`}
                  className="group block rounded-xl border border-[var(--color-line-soft)] bg-white/[0.015] p-3 transition hover:border-[rgba(200,255,114,0.22)]"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-[12.5px] font-medium text-[var(--color-text)]">
                        {proofCase.incident.title}
                      </p>
                      <p className="mt-0.5 text-[10.5px] text-[var(--color-dim)]">
                        {relativeTime(proofCase.incident.resolved_at)} ·{" "}
                        {proofCase.evidence.length} evidence items
                      </p>
                    </div>
                    <ConfidenceRing value={proofCase.incident.confidence} size={38} />
                  </div>
                  <div className="mt-3 flex items-center gap-2">
                    <span className="inline-flex items-center gap-1 rounded border border-[rgba(200,255,114,0.2)] px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-[var(--color-acid)]">
                      <IconCheck className="h-2.5 w-2.5" /> verified
                    </span>
                    <span className="mono truncate text-[9px] text-[var(--color-dim)]">
                      {proofCase.chain.evidence_valid ? "chain " : "broken "}
                      {proofCase.chain.evidence_checked}/{proofCase.chain.timeline_checked}
                    </span>
                  </div>
                  <div className="mt-3 space-y-1.5">
                    {proofCase.verifications.slice(0, 3).map((verification) => (
                      <div key={verification.id} className="flex items-center gap-2">
                        <span
                          className={cn(
                            "flex h-3.5 w-3.5 items-center justify-center rounded-full border",
                            verification.passed
                              ? "border-[rgba(200,255,114,0.35)] text-[var(--color-acid)]"
                              : "border-[rgba(255,111,97,0.35)] text-[var(--color-danger)]",
                          )}
                        >
                          {verification.passed ? (
                            <IconCheck className="h-2 w-2" />
                          ) : (
                            <IconAlert className="h-2 w-2" />
                          )}
                        </span>
                        <span className="truncate text-[10px] text-[var(--color-muted)]">
                          {verification.kind} · {verification.phase.toUpperCase()}
                        </span>
                      </div>
                    ))}
                  </div>
                </Link>
                <div className="mt-3">
                  <Meter value={1} />
                  <p className="mt-1.5 text-[9.5px] text-[var(--color-dim)]">
                    All required predicates passed before resolution.
                  </p>
                </div>
              </div>
            ) : (
              <div className="px-4 pb-4">
                <p className="text-[11.5px] leading-relaxed text-[var(--color-muted)]">
                  No proof yet. Run a fault in the studio to see a complete case file with
                  verification and a sealed chain.
                </p>
                <Link
                  href="/studio"
                  className="mt-3 inline-flex items-center gap-1.5 text-[11px] text-[var(--color-acid)] underline-grow"
                >
                  Run a fault <IconArrow className="h-3 w-3" />
                </Link>
              </div>
            )}
          </section>

          <section className="panel p-4">
            <SectionLabel>Last 24 hours</SectionLabel>
            <div className="mt-3 flex items-end justify-between gap-3">
              <div>
                <p className="mono text-[22px] leading-none text-[var(--color-text)]">
                  {items.length}
                </p>
                <p className="mt-1 text-[10px] text-[var(--color-dim)]">intakes recorded</p>
              </div>
              <Sparkline values={trend} />
            </div>
            <div className="mt-4 space-y-2">
              {classes.length === 0 && (
                <p className="text-[10.5px] text-[var(--color-dim)]">No class data yet.</p>
              )}
              {classes.map(([name, count]) => (
                <div key={name} className="flex items-center gap-2">
                  <span className="w-[104px] shrink-0 truncate text-[10.5px] text-[var(--color-muted)]">
                    {classLabel(name as never)}
                  </span>
                  <Meter value={count / Math.max(1, items.length)} tone="sky" />
                  <span className="mono w-5 text-right text-[9px] text-[var(--color-dim)]">
                    {count}
                  </span>
                </div>
              ))}
            </div>
          </section>
        </aside>
      </div>

      <footer className="mt-8 flex flex-col items-start justify-between gap-3 border-t border-[var(--color-line)] pt-5 text-[10.5px] text-[var(--color-dim)] sm:flex-row sm:items-center">
        <p className="flex items-center gap-2">
          <span className="status-dot live text-[var(--color-acid)]" />
          Resolution is a claim. Verification is the evidence.
        </p>
        <div className="flex flex-wrap items-center gap-4">
          <span className="mono">nightjar {meta?.version ?? "0.1.0"}</span>
          <Link href="/settings" className="underline-grow hover:text-[var(--color-muted)]">
            Safety model
          </Link>
          <Link href="/studio" className="underline-grow hover:text-[var(--color-muted)]">
            Demo studio
          </Link>
        </div>
      </footer>
    </div>
  );
}

function Kpi({
  label,
  value,
  caption,
  icon,
  href,
  spark,
  tone,
}: {
  label: string;
  value: number;
  caption: string;
  icon: React.ReactNode;
  href: string;
  spark: number[];
  tone: "acid" | "sky" | "amber" | "danger" | "muted";
}) {
  const color =
    tone === "danger"
      ? "text-[var(--color-danger)]"
      : tone === "amber"
        ? "text-[var(--color-amber)]"
        : tone === "sky"
          ? "text-[var(--color-sky)]"
          : tone === "muted"
            ? "text-[var(--color-muted)]"
            : "text-[var(--color-acid)]";
  return (
    <TiltCard className="panel h-full p-4">
      <a href={href} className="block h-full">
        <div className="flex items-center justify-between">
          <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--color-dim)]">
            {label}
          </span>
          <span className={cn("flex h-6 w-6 items-center justify-center rounded-md bg-white/[0.03]", color)}>
            {icon}
          </span>
        </div>
        <p className={cn("mono mt-3 text-[30px] leading-none tracking-tight", color)}>
          <Counter value={value} />
        </p>
        <p className="mt-1.5 truncate text-[10px] text-[var(--color-dim)]">{caption}</p>
        <div className="mt-3 opacity-70">
          <Sparkline values={spark} tone={tone === "danger" ? "danger" : tone === "sky" ? "sky" : "acid"} />
        </div>
      </a>
    </TiltCard>
  );
}

function Row({
  label,
  value,
  icon,
  good,
}: {
  label: string;
  value: string;
  icon: React.ReactNode;
  good?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-[var(--color-line-soft)] py-2 last:border-0">
      <span className="flex items-center gap-2 text-[11px] text-[var(--color-muted)]">
        <span className="text-[var(--color-dim)]">{icon}</span>
        {label}
      </span>
      <span
        className={cn(
          "mono text-[10px] uppercase tracking-wide",
          good === true
            ? "text-[var(--color-acid)]"
            : good === false
              ? "text-[var(--color-danger)]"
              : "text-[var(--color-dim)]",
        )}
      >
        {value}
      </span>
    </div>
  );
}
