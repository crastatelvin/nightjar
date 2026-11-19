from __future__ import annotations

import argparse
import asyncio
import json

from nightjar import __version__
from nightjar.api.settings import Settings
from nightjar.connectors.registry import build_registry
from nightjar.core.capabilities import V1_CLASSES, DoctorReport, assess
from nightjar.core.enums import IncidentClass
from nightjar.core.telemetry import DOCTOR_VERDICT_TOTAL, Telemetry


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    doctor = sub.add_parser(
        "doctor",
        help="probe every configured provider and report what is missing",
        description=(
            "Exit 0 only when every action required by the v1 incident classes has a "
            "healthy provider. Nothing is mutated; --deep also sends one signed "
            "webhook ping."
        ),
    )
    doctor.add_argument("--json", action="store_true", help="machine-readable output for CI")
    doctor.add_argument(
        "--deep",
        action="store_true",
        help="send a real ping to the notification webhook",
    )
    doctor.add_argument(
        "--class",
        dest="classes",
        action="append",
        choices=[item.value for item in V1_CLASSES],
        help="only require the actions for this incident class (repeatable)",
    )
    doctor.set_defaults(func=run)


async def _doctor(args: argparse.Namespace) -> DoctorReport:
    settings = Settings()
    registry = build_registry(settings)
    reports = await asyncio.gather(
        *(connector.preflight(deep=args.deep) for connector in registry.connectors)
    )
    classes: tuple[IncidentClass, ...] = (
        tuple(IncidentClass(value) for value in args.classes) if args.classes else V1_CLASSES
    )
    report = assess(
        registry.connectors,
        list(reports),
        environment=settings.environment,
        classes=classes,
    )
    # A verdict is a fact about a moment, not about a provider forever: exporting
    # it is what lets a dashboard show "the CDN has been degraded since Tuesday".
    telemetry = Telemetry.from_settings(settings)
    for item in report.providers:
        telemetry.count(DOCTOR_VERDICT_TOTAL, provider=item.provider, verdict=item.verdict)
    telemetry.shutdown()
    return report


def _render(report: DoctorReport) -> str:
    lines: list[str] = []
    lines.append(f"Nightjar {__version__} — {report.environment} configuration")
    lines.append("")
    lines.append(f"{'provider':<12} {'verdict':<10} {'identity':<34} detail")
    lines.append(f"{'-' * 12} {'-' * 10} {'-' * 34} {'-' * 40}")
    for item in report.providers:
        identity = item.identity if len(item.identity) <= 34 else item.identity[:31] + "..."
        lines.append(f"{item.provider:<12} {item.verdict:<10} {identity:<34} {item.detail}")
    lines.append("")
    lines.append(f"{'action':<22} {'provider':<12} {'mode':<8} verdict")
    lines.append(f"{'-' * 22} {'-' * 12} {'-' * 8} {'-' * 20}")
    for action in report.actions:
        mode = "auto" if action.automated else "manual"
        lines.append(
            f"{action.action_type.value:<22} {action.provider:<12} {mode:<8} {action.verdict}"
        )
    for missing_action in report.missing:
        lines.append(f"{missing_action.value:<22} {'—':<12} {'—':<8} missing")
    lines.append("")
    if report.ok:
        lines.append("OK: every v1 incident class can be closed and verified.")
    else:
        lines.append("NOT READY:")
        lines.extend(f"  - {problem}" for problem in report.problems)
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    report = asyncio.run(_doctor(args))
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(_render(report))
    return 0 if report.ok else 1
