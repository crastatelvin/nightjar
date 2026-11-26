"""``nightjarctl capabilities`` — what this deployment can do, and how it proves it.

The same catalogue is served at ``/v1/meta/capabilities``. This command exists so
the README table and an operator's terminal are rendered from the connector code
rather than typed by hand: a provider that gains an action, loses one, or changes
its read-back changes the output, and CI fails until the page is regenerated.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nightjar.api.settings import Settings
from nightjar.connectors.registry import build_registry
from nightjar.core.capabilities import ActionCapability, assess
from nightjar.core.catalogue import extract_block, render_block, render_table, replace_block


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "capabilities",
        help="list every action this deployment can perform and the proof it uses",
        description=(
            "Rendered from the connector capabilities, not from prose. --all shows "
            "every supported provider even when this deployment has not configured "
            "it, which is what the README table uses."
        ),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="include providers this deployment has not configured",
    )
    parser.add_argument("--markdown", action="store_true", help="render the README table")
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--check",
        type=Path,
        metavar="FILE",
        help="exit 1 when the marked table in FILE does not match the code",
    )
    target.add_argument(
        "--write",
        type=Path,
        metavar="FILE",
        help="rewrite the marked table in FILE",
    )
    parser.set_defaults(func=run)


def _catalogue(include_unconfigured: bool) -> list[ActionCapability]:
    settings = Settings()
    registry = build_registry(settings, include_unconfigured=include_unconfigured)
    report = assess(
        registry.connectors,
        [],
        environment=settings.environment,
        probed=False,
    )
    return report.actions


def run(args: argparse.Namespace) -> int:
    # A file operation always describes the whole supported surface: the README
    # is not a report about the machine that happened to render it.
    include_unconfigured = args.all or args.check is not None or args.write is not None
    actions = _catalogue(include_unconfigured)

    if args.check is not None:
        try:
            current = extract_block(args.check.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"error: {args.check}: {exc}", file=sys.stderr)
            return 1
        expected = render_block(actions)
        if current != expected:
            print(
                f"error: {args.check} is out of date; run "
                f"`nightjarctl capabilities --write {args.check}`",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {args.check} matches the connector capabilities.")
        return 0

    if args.write is not None:
        text = args.write.read_text(encoding="utf-8")
        args.write.write_text(replace_block(text, render_block(actions)), encoding="utf-8")
        print(f"Wrote {args.write}.")
        return 0

    if args.markdown:
        print(render_block(actions))
        return 0

    print(render_table(actions))
    return 0


__all__ = ["add_parser", "run"]
