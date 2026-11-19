"""Verify the evidence and timeline hash chains of every incident.

The ledger is the product's claim to truth: an action was performed, a check
passed, a human approved it. Each of those is a row sealed into a hash chain, so
tampering with a row breaks the chain from that row onward. Verification answers
one question per incident — does the chain still add up? — and this module asks
it for every incident in the database, because a restore that verified only the
configured tenant would leave the rest unchecked.

The exit code is the contract: zero means every chain is intact. It is used by
the restore script after `pg_restore`, by the nightly check in CI, and by an
operator who wants to know whether the database they are holding is the one the
product wrote.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any

from nightjar.api import db
from nightjar.api.repository import Repository

DEFAULT_DATABASE_URL = "postgresql+asyncpg://nightjar@127.0.0.1:55432/nightjar"
PAGE_SIZE = 200


@dataclass
class BrokenChain:
    incident_id: str
    chain: str
    checked: int
    broken_at: str | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "chain": self.chain,
            "checked": self.checked,
            "broken_at": self.broken_at,
            "reason": self.reason,
        }


@dataclass
class LedgerReport:
    database: str
    incidents: int = 0
    timeline_events: int = 0
    evidence_items: int = 0
    broken: list[BrokenChain] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.broken

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "database": self.database,
            "incidents": self.incidents,
            "timeline_events": self.timeline_events,
            "evidence_items": self.evidence_items,
            "broken": [item.as_dict() for item in self.broken],
        }


async def verify_database(database_url: str) -> LedgerReport:
    """Walk every incident and verify both chains.

    The database is configured per call and disposed afterwards: this runs as a
    one-shot command, and a ledger check must never leave a pool behind for the
    next thing the process does.
    """
    report = LedgerReport(database=database_url)
    db.configure(database_url)
    try:
        repo = Repository(db.get_session_factory())
        offset = 0
        while True:
            incidents = await repo.list_all_incidents(limit=PAGE_SIZE, offset=offset)
            if not incidents:
                break
            for incident in incidents:
                evidence, timeline = await repo.verify_chains(incident.id)
                report.incidents += 1
                report.evidence_items += evidence.checked
                report.timeline_events += timeline.checked
                if not evidence.valid:
                    report.broken.append(
                        BrokenChain(
                            incident_id=incident.id,
                            chain="evidence",
                            checked=evidence.checked,
                            broken_at=evidence.broken_at,
                            reason=evidence.reason or "evidence chain mismatch",
                        )
                    )
                if not timeline.valid:
                    report.broken.append(
                        BrokenChain(
                            incident_id=incident.id,
                            chain="timeline",
                            checked=timeline.checked,
                            broken_at=timeline.broken_at,
                            reason=timeline.reason or "timeline chain mismatch",
                        )
                    )
            offset += len(incidents)
    finally:
        await db.dispose()
    return report


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "verify-ledger",
        help="verify every incident's evidence and timeline hash chains",
        description=(
            "Walk every incident in the database and recompute both hash chains. "
            "Exit 0 only when every chain is intact; a tampered or truncated row "
            "breaks the chain from that point and is reported with its id."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL to verify (default: $NIGHTJAR_DATABASE_URL)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output for CI")
    parser.set_defaults(func=run)


def _render(report: LedgerReport) -> str:
    lines = [
        f"ledger: {report.database}",
        f"  incidents       {report.incidents}",
        f"  timeline events {report.timeline_events}",
        f"  evidence items  {report.evidence_items}",
    ]
    if report.broken:
        lines.append("")
        lines.append("BROKEN CHAINS:")
        for item in report.broken:
            lines.append(
                f"  {item.incident_id} {item.chain} at {item.broken_at or '?'}: {item.reason}"
            )
        lines.append("")
        lines.append("FAIL: the ledger has been altered or truncated.")
    else:
        lines.append("")
        lines.append("OK: every chain is intact.")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    import asyncio
    import json
    import os

    database_url = (
        args.database_url or os.environ.get("NIGHTJAR_DATABASE_URL") or DEFAULT_DATABASE_URL
    )
    report = asyncio.run(verify_database(database_url))
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(_render(report))
    return 0 if report.ok else 1


__all__ = ["BrokenChain", "LedgerReport", "add_parser", "run", "verify_database"]
