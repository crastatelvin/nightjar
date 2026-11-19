"""``nightjarctl`` — the operator's command line.

Two commands, both of which answer a question an operator has before or after an
incident rather than during one: ``doctor`` asks whether the deployment can
actually close each v1 incident class, and ``verify-ledger`` asks whether the
stored evidence still adds up.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from nightjar import __version__
from nightjar.cli import doctor, ledger


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nightjarctl",
        description="Operate Nightjar: check what it can really do, and verify its ledger.",
    )
    parser.add_argument("--version", action="version", version=f"nightjar {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor.add_parser(sub)
    ledger.add_parser(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
