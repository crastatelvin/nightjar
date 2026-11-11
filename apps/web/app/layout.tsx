import type { Metadata, Viewport } from "next";
import "./globals.css";
import { AmbientCanvas } from "@/components/ambient-background";
import { CursorGlow } from "@/components/cursor-glow";
import { AppShell } from "@/components/app-shell";
import { SystemProvider } from "@/components/system-context";
import { ToastProvider } from "@/components/toast";

export const metadata: Metadata = {
  title: {
    default: "Nightjar — incident response that proves it fixed the problem",
    template: "%s · Nightjar",
  },
  description:
    "Self-hosted incident response for small teams. Typed actions, deterministic policy, human approval, and verification that does not take the model's word for it.",
  applicationName: "Nightjar",
  robots: { index: false, follow: false },
  openGraph: {
    title: "Nightjar",
    description: "Open-source incident response that proves it fixed the problem.",
    type: "website",
  },
};

export const viewport: Viewport = {
  themeColor: "#05070c",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <AmbientCanvas />
        <CursorGlow />
        <div className="grain" aria-hidden="true" />
        <ToastProvider>
          <SystemProvider>
            <AppShell>{children}</AppShell>
          </SystemProvider>
        </ToastProvider>
      </body>
    </html>
  );
}
