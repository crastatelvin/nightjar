"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { DEFAULT_API_BASE, getActor, getApiBase, getToken } from "@/lib/api";
import { cn, titleCase } from "@/lib/format";
import { useSystem } from "@/components/system-context";
import { useToast } from "@/components/toast";
import { SectionLabel } from "@/components/primitives";
import {
  IconAlert,
  IconCheck,
  IconCloud,
  IconKey,
  IconLock,
  IconRefresh,
  IconServer,
  IconSettings,
  IconShield,
  IconZap,
} from "@/components/icons";

type TestState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "ok"; detail: string }
  | { kind: "error"; detail: string };

export default function SettingsPage() {
  const toast = useToast();
  const { meta, online, checkedAt, refresh } = useSystem();
  const [apiBase, setApiBase] = useState(DEFAULT_API_BASE);
  const [token, setToken] = useState("");
  const [actor, setActor] = useState("operator");
  const [showToken, setShowToken] = useState(false);
  const [test, setTest] = useState<TestState>({ kind: "idle" });
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setApiBase(getApiBase());
    setToken(getToken());
    setActor(getActor());
  }, []);

  const save = () => {
    const trimmed = apiBase.trim().replace(/\/$/, "") || DEFAULT_API_BASE;
    window.localStorage.setItem("nightjar.apiBase", trimmed);
    window.localStorage.setItem("nightjar.token", token.trim());
    window.localStorage.setItem("nightjar.actor", actor.trim() || "operator");
    setApiBase(trimmed);
    setDirty(false);
    setTest({ kind: "idle" });
    window.dispatchEvent(new Event("nightjar:settings"));
    toast("Connection settings saved in this browser.", "good");
  };

  const clear = () => {
    window.localStorage.removeItem("nightjar.apiBase");
    window.localStorage.removeItem("nightjar.token");
    window.localStorage.removeItem("nightjar.actor");
    setApiBase(DEFAULT_API_BASE);
    setToken("");
    setActor("operator");
    setDirty(false);
    setTest({ kind: "idle" });
    window.dispatchEvent(new Event("nightjar:settings"));
    toast("Local connection settings cleared.", "good");
  };

  const probe = async () => {
    setTest({ kind: "running" });
    const base = (apiBase.trim().replace(/\/$/, "") || DEFAULT_API_BASE);
    const headers: HeadersInit = token.trim()
      ? { authorization: `Bearer ${token.trim()}` }
      : {};
    try {
      const health = await fetch(`${base}/healthz`, { cache: "no-store" });
      if (!health.ok) {
        setTest({ kind: "error", detail: `healthz returned ${health.status}` });
        return;
      }
      const metaResponse = await fetch(`${base}/v1/meta`, {
        headers,
        cache: "no-store",
      });
      if (metaResponse.status === 401 || metaResponse.status === 403) {
        setTest({ kind: "error", detail: "API reachable, but the token was rejected." });
        return;
      }
      if (!metaResponse.ok) {
        setTest({ kind: "error", detail: `meta returned ${metaResponse.status}` });
        return;
      }
      const info = (await metaResponse.json()) as {
        provider?: string;
        model?: string;
        version?: string;
      };
      setTest({
        kind: "ok",
        detail: `${info.provider ?? "unknown"} / ${info.model ?? "unknown"} · v${
          info.version ?? "?"
        }`,
      });
      refresh();
    } catch (error) {
      setTest({
        kind: "error",
        detail: error instanceof Error ? error.message : "Connection failed",
      });
    }
  };

  const catalog = Object.entries(meta?.action_catalog ?? {}).sort((a, b) =>
    a[1].localeCompare(b[1]),
  );

  return (
    <div className="mx-auto max-w-[1180px] px-4 pb-12 pt-6 sm:px-6 lg:px-8">
      <header className="max-w-[70ch]">
        <span className="inline-flex items-center gap-2 rounded-full border border-[var(--color-line)] bg-white/[0.02] px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--color-dim)]">
          <IconSettings className="h-3 w-3" />
          Connection
        </span>
        <h1 className="mt-4 text-balance text-[clamp(1.8rem,3.8vw,2.6rem)] font-semibold leading-[1.05] tracking-[-0.04em]">
          Point the UI at your control plane.
        </h1>
        <p className="mt-3 text-[13px] leading-relaxed text-[var(--color-muted)]">
          The browser talks to the API directly. These values live in this browser only —
          they are not written to the server and never leave the machine except as the
          Authorization header for the address you choose.
        </p>
      </header>

      <div className="mt-5 grid items-start gap-4 lg:grid-cols-[1.05fr_0.95fr]">
        <section className="panel p-4 sm:p-5">
          <SectionLabel>Browser connection</SectionLabel>

          <label className="mt-4 block text-[10.5px] font-medium text-[var(--color-muted)]">
            API base URL
            <input
              value={apiBase}
              onChange={(event) => {
                setApiBase(event.target.value);
                setDirty(true);
              }}
              spellCheck={false}
              placeholder="http://127.0.0.1:8000"
              className="mono mt-1.5 h-10 w-full rounded-lg border border-[var(--color-line)] bg-[var(--color-ink)] px-3 text-[12px] text-[var(--color-text)] outline-none transition focus:border-[rgba(200,255,114,0.4)]"
            />
          </label>

          <label className="mt-3 block text-[10.5px] font-medium text-[var(--color-muted)]">
            Bearer token
            <div className="relative mt-1.5">
              <input
                value={token}
                onChange={(event) => {
                  setToken(event.target.value);
                  setDirty(true);
                }}
                type={showToken ? "text" : "password"}
                spellCheck={false}
                autoComplete="off"
                placeholder="empty if NIGHTJAR_API_TOKEN is unset"
                className="mono h-10 w-full rounded-lg border border-[var(--color-line)] bg-[var(--color-ink)] px-3 pr-16 text-[12px] text-[var(--color-text)] outline-none transition placeholder:text-[var(--color-dim)]/70 focus:border-[rgba(200,255,114,0.4)]"
              />
              <button
                type="button"
                onClick={() => setShowToken((value) => !value)}
                className="absolute right-1.5 top-1/2 h-7 -translate-y-1/2 rounded-md px-2 text-[9.5px] uppercase tracking-wide text-[var(--color-dim)] transition hover:text-[var(--color-muted)]"
              >
                {showToken ? "hide" : "show"}
              </button>
            </div>
          </label>

          <label className="mt-3 block text-[10.5px] font-medium text-[var(--color-muted)]">
            Actor name
            <input
              value={actor}
              onChange={(event) => {
                setActor(event.target.value);
                setDirty(true);
              }}
              maxLength={100}
              className="mt-1.5 h-10 w-full rounded-lg border border-[var(--color-line)] bg-[var(--color-ink)] px-3 text-[12px] text-[var(--color-text)] outline-none transition focus:border-[rgba(200,255,114,0.4)]"
            />
            <span className="mt-1 block text-[9.5px] font-normal leading-relaxed text-[var(--color-dim)]">
              Sent as <span className="mono">x-nightjar-actor</span> on approvals and
              notes. It is self-asserted; deploy behind an authenticating proxy if identity
              matters.
            </span>
          </label>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={save}
              className={cn(
                "inline-flex h-9 items-center gap-1.5 rounded-lg border px-3.5 text-[12px] font-semibold transition",
                dirty
                  ? "border-[rgba(200,255,114,0.32)] bg-[rgba(200,255,114,0.09)] text-[var(--color-acid)] hover:bg-[rgba(200,255,114,0.14)]"
                  : "border-[var(--color-line)] text-[var(--color-muted)] hover:text-[var(--color-text)]",
              )}
            >
              <IconCheck className="h-3.5 w-3.5" />
              Save
            </button>
            <button
              type="button"
              onClick={() => void probe()}
              disabled={test.kind === "running"}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-[var(--color-line)] px-3.5 text-[12px] text-[var(--color-muted)] transition hover:border-[#2b3a50] hover:text-[var(--color-text)] disabled:opacity-50"
            >
              {test.kind === "running" ? (
                <span className="h-3 w-3 animate-spin-slow rounded-full border border-current border-t-transparent" />
              ) : (
                <IconZap className="h-3.5 w-3.5" />
              )}
              Test connection
            </button>
            <button
              type="button"
              onClick={clear}
              className="ml-auto text-[10.5px] text-[var(--color-dim)] underline-grow hover:text-[var(--color-danger)]"
            >
              Clear local settings
            </button>
          </div>

          {test.kind === "ok" && (
            <p className="animate-fade mt-3 flex items-center gap-2 rounded-lg border border-[rgba(200,255,114,0.24)] bg-[rgba(200,255,114,0.04)] px-3 py-2 text-[11px] text-[var(--color-acid)]">
              <IconCheck className="h-3.5 w-3.5 shrink-0" />
              Connected · {test.detail}
            </p>
          )}
          {test.kind === "error" && (
            <p className="animate-fade mt-3 flex items-start gap-2 rounded-lg border border-[rgba(255,111,97,0.28)] bg-[rgba(255,111,97,0.05)] px-3 py-2 text-[11px] text-[var(--color-danger)]">
              <IconAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {test.detail}
            </p>
          )}

          <p className="mt-4 flex items-start gap-1.5 border-t border-[var(--color-line-soft)] pt-3 text-[9.5px] leading-relaxed text-[var(--color-dim)]">
            <IconLock className="mt-0.5 h-3 w-3 shrink-0" />
            The token is stored in localStorage for this browser profile. That is
            convenient and not a security boundary: any script on this origin can read it.
            Use a dedicated profile, keep the UI off the public internet, and terminate
            authentication at a proxy.
          </p>
        </section>

        <section className="flex flex-col gap-4">
          <div className="panel p-4 sm:p-5">
            <div className="flex items-center justify-between gap-3">
              <SectionLabel className="flex-1">Control plane</SectionLabel>
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[9.5px]",
                  online
                    ? "border-[rgba(200,255,114,0.24)] text-[var(--color-acid)]"
                    : "border-[rgba(255,111,97,0.3)] text-[var(--color-danger)]",
                )}
              >
                <span className={cn("status-dot", online ? "text-[var(--color-acid)] live" : "text-[var(--color-danger)]")} />
                {online ? "online" : "offline"}
              </span>
            </div>

            <dl className="mt-3 flex flex-col gap-0.5 text-[11px]">
              <Row label="Version" value={meta?.version ?? "—"} />
              <Row label="Provider" value={meta?.provider ?? "—"} />
              <Row label="Model" value={meta?.model ?? "—"} mono />
              <Row label="Tools" value={meta ? String(meta.tools.length) : "—"} />
              <Row
                label="Last checked"
                value={
                  checkedAt
                    ? new Date(checkedAt).toLocaleTimeString(undefined, {
                        hour: "2-digit",
                        minute: "2-digit",
                        second: "2-digit",
                      })
                    : "—"
                }
              />
            </dl>

            <div className="mt-3 flex flex-wrap gap-1.5">
              {(meta?.connectors ?? []).map((connector) => (
                <span
                  key={connector}
                  className="mono inline-flex items-center gap-1 rounded border border-[var(--color-line)] bg-white/[0.02] px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-[var(--color-dim)]"
                >
                  <IconCloud className="h-2.5 w-2.5" />
                  {connector}
                </span>
              ))}
              {!meta && (
                <span className="text-[10px] text-[var(--color-dim)]">
                  No metadata yet. Save a reachable API address.
                </span>
              )}
            </div>

            <button
              type="button"
              onClick={refresh}
              className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-lg border border-[var(--color-line)] px-2.5 text-[10.5px] text-[var(--color-muted)] transition hover:border-[#2b3a50] hover:text-[var(--color-text)]"
            >
              <IconRefresh className="h-3 w-3" />
              Refresh metadata
            </button>
          </div>

          <div className="panel p-4 sm:p-5">
            <SectionLabel>Demo proxy (server side)</SectionLabel>
            <p className="mt-2 text-[11px] leading-relaxed text-[var(--color-muted)]">
              The Fault studio calls the lab and API from a Next.js route, not from the
              browser, so the lab port does not need CORS. Configure it in the web
              environment and restart the server:
            </p>
            <div className="mono mt-2.5 flex flex-col gap-1 rounded-lg border border-[var(--color-line-soft)] bg-[#070b12] p-2.5 text-[10px] text-[var(--color-muted)]">
              <span>NIGHTJAR_API_URL=http://127.0.0.1:8000</span>
              <span>NIGHTJAR_LAB_URL=http://127.0.0.1:8100</span>
              <span>NIGHTJAR_API_TOKEN=</span>
            </div>
            <p className="mt-2 text-[9.5px] leading-relaxed text-[var(--color-dim)]">
              When the API token is set on the server, the studio proxy attaches it. The
              browser token above is only for case-file reads and decisions.
            </p>
          </div>
        </section>
      </div>

      <section className="mt-5">
        <SectionLabel>Safety model</SectionLabel>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          {[
            {
              icon: IconShield,
              title: "Typed actions only",
              body: "The model emits actions from a fixed catalogue. There is no shell, no arbitrary URL, and no free-form code path.",
            },
            {
              icon: IconLock,
              title: "Policy decides, not the model",
              body: "A deterministic engine returns allow, approve, or deny for every mutating action. Errors deny. R2 needs a human; R3 needs break-glass and two approvals.",
            },
            {
              icon: IconCheck,
              title: "Verification closes the loop",
              body: "Resolution requires a stored plan, an executed action, passing predicates at T+0, and a clean stability window. Failure reopens the case.",
            },
          ].map((item) => {
            const Icon = item.icon;
            return (
              <div key={item.title} className="panel p-4">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--color-line)] bg-[#070b12] text-[var(--color-acid)]">
                  <Icon className="h-4 w-4" />
                </span>
                <p className="mt-2.5 text-[12.5px] font-semibold text-[var(--color-text)]">
                  {item.title}
                </p>
                <p className="mt-1 text-[11px] leading-relaxed text-[var(--color-muted)]">
                  {item.body}
                </p>
              </div>
            );
          })}
        </div>
      </section>

      <section className="panel mt-4 overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-line)] p-4">
          <div>
            <SectionLabel>Action catalogue</SectionLabel>
            <p className="mt-2 text-[11px] text-[var(--color-muted)]">
              Every capability the planner can request. Anything not on this list is
              rejected before policy runs.
            </p>
          </div>
          <span className="mono text-[10px] text-[var(--color-dim)]">
            {catalog.length} typed actions
          </span>
        </div>

        {catalog.length === 0 ? (
          <p className="p-4 text-[11.5px] text-[var(--color-dim)]">
            Connect to the API to load the catalogue.
          </p>
        ) : (
          <div className="grid gap-px bg-[var(--color-line-soft)] sm:grid-cols-2 lg:grid-cols-3">
            {catalog.map(([action, risk]) => (
              <div
                key={action}
                className="flex items-center justify-between gap-3 bg-[var(--color-panel)] px-4 py-2.5"
              >
                <span className="mono flex items-center gap-2 text-[11px] text-[var(--color-muted)]">
                  <IconServer className="h-3 w-3 text-[var(--color-dim)]" />
                  {action}
                </span>
                <span
                  className={cn(
                    "mono rounded border px-1.5 py-0.5 text-[9px] uppercase",
                    risk === "r2"
                      ? "border-[rgba(255,196,107,0.28)] text-[var(--color-amber)]"
                      : risk === "r3"
                        ? "border-[rgba(255,111,97,0.35)] text-[var(--color-danger)]"
                        : risk === "r1"
                          ? "border-[rgba(110,240,194,0.25)] text-[var(--color-mint)]"
                          : "border-[var(--color-line)] text-[var(--color-dim)]",
                  )}
                >
                  {titleCase(risk)}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--color-line)] pt-4 text-[10px] text-[var(--color-dim)]">
        <p className="flex items-center gap-2">
          <IconKey className="h-3 w-3" />
          Provider and model are server configuration, not browser state.
        </p>
        <Link href="/studio" className="underline-grow hover:text-[var(--color-muted)]">
          Open the fault studio
        </Link>
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-[var(--color-line-soft)] py-1.5 last:border-0">
      <dt className="text-[var(--color-dim)]">{label}</dt>
      <dd className={cn("truncate text-right text-[var(--color-muted)]", mono && "mono text-[10px]")}>
        {value}
      </dd>
    </div>
  );
}
