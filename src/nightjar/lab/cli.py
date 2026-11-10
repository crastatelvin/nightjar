"""``nightjar-lab``: launch the synthetic CornerShop environment."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn

from nightjar.lab.cornershop.app import LAB, SCENARIOS, app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nightjar-lab",
        description=(
            "Run the synthetic CornerShop storefront used by Nightjar tests and "
            "evaluations. All data is fake; phishing domains use reserved .example."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--log-level", default="info")
    parser.add_argument(
        "--scenario",
        choices=SCENARIOS,
        default=None,
        help="inject this fault as soon as the lab starts",
    )
    parser.add_argument(
        "--variant",
        type=int,
        default=0,
        help="variant for --scenario (default: 0)",
    )
    parser.add_argument(
        "--injection",
        action="store_true",
        help="attach the prompt-injection corpus to the seeded scenario",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.scenario:
        base_url = f"http://{args.host}:{args.port}"
        seed = LAB.inject(
            args.scenario,
            args.variant,
            base_url=base_url,
            injection=args.injection,
        )
        print(f"injected {args.scenario} v{args.variant} into {base_url}")
        print(f"title: {seed['title']}")
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

__all__ = ["build_parser", "main"]
