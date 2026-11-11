import Link from "next/link";
import { IconArrow } from "@/components/icons";
import { Logo } from "@/components/logo";

export default function NotFound() {
  return (
    <div className="mx-auto flex min-h-[70vh] max-w-xl flex-col items-center justify-center px-6 text-center">
      <Logo size={44} className="animate-float" />
      <p className="mono mt-8 text-[11px] uppercase tracking-[0.3em] text-[var(--color-dim)]">
        404 · no such record
      </p>
      <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em]">
        This case file is not here.
      </h1>
      <p className="mt-3 text-sm leading-relaxed text-[var(--color-muted)]">
        It may have been an incident that never existed, or a link from a different
        deployment. The ledger only contains what was actually observed.
      </p>
      <Link
        href="/"
        className="group mt-7 inline-flex items-center gap-2 rounded-lg border border-[rgba(200,255,114,0.3)] bg-[rgba(200,255,114,0.07)] px-4 py-2.5 text-sm font-medium text-[var(--color-acid)]"
      >
        Back to the command deck
        <IconArrow className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
      </Link>
    </div>
  );
}
