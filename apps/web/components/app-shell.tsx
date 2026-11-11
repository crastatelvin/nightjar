"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/format";
import {
  IconArrow,
  IconCommand,
  IconFlask,
  IconGrid,
  IconMenu,
  IconSearch,
  IconSettings,
  IconX,
} from "./icons";
import { CommandPalette } from "./command-palette";
import { useSystem } from "./system-context";
import { Wordmark } from "./logo";

const NAV = [
  { href: "/", label: "Command deck", icon: IconGrid },
  { href: "/studio", label: "Fault studio", icon: IconFlask },
  { href: "/settings", label: "Settings", icon: IconSettings },
];

function sectionFor(pathname: string): string {
  if (pathname.startsWith("/incidents/")) return "Case file";
  if (pathname.startsWith("/studio")) return "Fault studio";
  if (pathname.startsWith("/settings")) return "Settings";
  return "Command deck";
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const { meta, online, checkedAt } = useSystem();

  const openPalette = useCallback(() => setPaletteOpen(true), []);
  const closePalette = useCallback(() => setPaletteOpen(false), []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  const section = sectionFor(pathname);

  return (
    <div className="relative min-h-dvh">
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-lg focus:bg-[var(--color-acid)] focus:px-3 focus:py-2 focus:text-sm focus:font-semibold focus:text-[#07110a]"
      >
        Skip to content
      </a>

      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[76px] flex-col items-center border-r border-[var(--color-line)] bg-[#070a11]/80 py-5 backdrop-blur-xl lg:flex">
        <Link href="/" aria-label="Nightjar home" className="group relative">
          <span className="absolute -inset-3 rounded-xl bg-[var(--color-acid)]/0 transition group-hover:bg-[var(--color-acid)]/5" />
          <Wordmark compact />
        </Link>

        <div className="mt-9 flex flex-col items-center gap-1.5">
          {NAV.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-label={item.label}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "group relative flex h-11 w-11 items-center justify-center rounded-xl transition-colors duration-200",
                  active
                    ? "bg-[rgba(200,255,114,0.08)] text-[var(--color-acid)]"
                    : "text-[var(--color-dim)] hover:bg-white/[0.035] hover:text-[var(--color-text)]",
                )}
              >
                {active && (
                  <span className="absolute -left-4 h-5 w-[2px] rounded-full bg-[var(--color-acid)] shadow-[0_0_12px_rgba(200,255,114,0.7)]" />
                )}
                <Icon className="h-[18px] w-[18px]" />
                <span className="pointer-events-none absolute left-[52px] z-50 whitespace-nowrap rounded-md border border-[var(--color-line)] bg-[var(--color-panel-2)] px-2 py-1 text-[11px] text-[var(--color-text)] opacity-0 shadow-xl transition-all duration-150 group-hover:translate-x-0.5 group-hover:opacity-100">
                  {item.label}
                </span>
              </Link>
            );
          })}
        </div>

        <div className="mt-auto flex flex-col items-center gap-4">
          <button
            type="button"
            onClick={openPalette}
            aria-label="Open command palette"
            className="flex h-10 w-10 items-center justify-center rounded-xl border border-[var(--color-line)] text-[var(--color-dim)] transition hover:border-[#2b3a50] hover:text-[var(--color-text)]"
          >
            <IconCommand className="h-4 w-4" />
          </button>
          <div
            className="flex h-9 w-9 items-center justify-center rounded-full border border-[var(--color-line)] bg-[var(--color-ink)]"
            title={online ? "API connected" : "API unreachable"}
          >
            <span
              className={cn(
                "status-dot",
                online ? "text-[var(--color-acid)] live" : "text-[var(--color-danger)]",
              )}
            />
          </div>
        </div>
      </aside>

      <header className="sticky top-0 z-30 border-b border-[var(--color-line)] bg-[#060910]/72 backdrop-blur-xl lg:pl-[76px]">
        <div className="flex h-[68px] items-center gap-3 px-4 sm:px-6">
          <button
            type="button"
            onClick={() => setMenuOpen(true)}
            aria-label="Open navigation"
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--color-line)] text-[var(--color-muted)] lg:hidden"
          >
            <IconMenu className="h-4 w-4" />
          </button>

          <div className="lg:hidden">
            <Wordmark />
          </div>

          <div className="hidden items-center gap-2 text-[12px] lg:flex">
            <span className="text-[var(--color-dim)]">nightjar</span>
            <span className="text-[var(--color-dim)]/50">/</span>
            <span className="font-medium text-[var(--color-text)]">{section}</span>
          </div>

          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            <div className="hidden items-center gap-2 rounded-full border border-[var(--color-line)] bg-white/[0.02] px-3 py-1.5 text-[11px] sm:flex">
              <span
                className={cn(
                  "status-dot",
                  online ? "text-[var(--color-acid)] live" : "text-[var(--color-danger)]",
                )}
              />
              <span className="mono text-[var(--color-muted)]">
                {online ? meta?.provider ?? "online" : "offline"}
              </span>
              {online && meta && (
                <>
                  <span className="text-[var(--color-line)]">·</span>
                  <span className="mono text-[var(--color-dim)]">{meta.model}</span>
                </>
              )}
            </div>

            <button
              type="button"
              onClick={openPalette}
              className="group flex h-9 items-center gap-2 rounded-lg border border-[var(--color-line)] bg-white/[0.02] px-2.5 text-[12px] text-[var(--color-dim)] transition hover:border-[#2b3a50] hover:text-[var(--color-muted)]"
            >
              <IconSearch className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Search</span>
              <span className="kbd ml-1 hidden sm:inline-flex">⌘K</span>
            </button>

            <Link
              href="/studio"
              className="group flex h-9 items-center gap-1.5 rounded-lg border border-[rgba(200,255,114,0.28)] bg-[rgba(200,255,114,0.07)] px-3 text-[12px] font-medium text-[var(--color-acid)] transition hover:bg-[rgba(200,255,114,0.12)]"
            >
              <span className="status-dot live" />
              <span className="hidden sm:inline">Simulate</span>
              <IconArrow className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
            </Link>
          </div>
        </div>
        {checkedAt !== null && !online && (
          <div className="border-t border-[rgba(255,111,97,0.2)] bg-[rgba(255,111,97,0.06)] px-4 py-1.5 text-[11px] text-[var(--color-danger)] sm:px-6">
            API unreachable at the configured address. Start `nightjar-api` or update the
            connection in Settings.
          </div>
        )}
      </header>

      <main id="content" className="relative z-10 pb-16 lg:pl-[76px]">
        {children}
      </main>

      {menuOpen && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true">
          <button
            type="button"
            className="absolute inset-0 bg-black/70 backdrop-blur-sm animate-fade"
            aria-label="Close menu"
            onClick={() => setMenuOpen(false)}
          />
          <div className="animate-rise absolute inset-y-0 left-0 w-[82%] max-w-[320px] border-r border-[var(--color-line)] bg-[#080c14] p-5">
            <div className="flex items-center justify-between">
              <Wordmark />
              <button
                type="button"
                onClick={() => setMenuOpen(false)}
                aria-label="Close menu"
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--color-line)] text-[var(--color-muted)]"
              >
                <IconX className="h-4 w-4" />
              </button>
            </div>
            <nav className="mt-8 flex flex-col gap-1">
              {NAV.map((item) => {
                const Icon = item.icon;
                const active =
                  item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      "flex items-center gap-3 rounded-xl px-3 py-3 text-sm",
                      active
                        ? "bg-[rgba(200,255,114,0.07)] text-[var(--color-acid)]"
                        : "text-[var(--color-muted)] hover:bg-white/[0.03]",
                    )}
                  >
                    <Icon className="h-4 w-4" />
                    {item.label}
                  </Link>
                );
              })}
            </nav>
            <div className="mt-8 rounded-xl border border-[var(--color-line)] p-3">
              <p className="text-[10px] uppercase tracking-[0.16em] text-[var(--color-dim)]">
                Control plane
              </p>
              <div className="mt-2 flex items-center gap-2 text-[12px] text-[var(--color-muted)]">
                <span
                  className={cn(
                    "status-dot",
                    online ? "text-[var(--color-acid)] live" : "text-[var(--color-danger)]",
                  )}
                />
                {online ? "connected" : "offline"}
              </div>
              <p className="mono mt-1 text-[11px] text-[var(--color-dim)]">
                {meta ? `${meta.provider} / ${meta.model}` : "no metadata"}
              </p>
            </div>
          </div>
        </div>
      )}

      <CommandPalette open={paletteOpen} onClose={closePalette} />
    </div>
  );
}
