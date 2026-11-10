"""Scenario catalogue loading.

The manifest in ``evals/scenarios/cornershop.yaml`` is the source of truth for
case counts and expectations. Cases are expanded from compact matrices so the
five failure modes can vary independently without duplicating forty lines.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Any

import yaml

from nightjar.evals.models import EvalCase

MANIFEST_NAME = "cornershop.yaml"
REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "evals" / "scenarios" / MANIFEST_NAME


def default_manifest_path() -> Path | None:
    """Locate the packaged manifest, then the checkout-relative manifest."""
    if MANIFEST_PATH.is_file():
        return MANIFEST_PATH
    try:
        resource = importlib.resources.files("nightjar.evals").joinpath("scenarios", MANIFEST_NAME)
    except ModuleNotFoundError:  # pragma: no cover - package layout guard
        return None
    if resource.is_file():
        return Path(str(resource))
    return None


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    source = path or default_manifest_path()
    if source is None:
        raise FileNotFoundError(
            "Nightjar eval manifest not found; pass --scenarios evals/scenarios/cornershop.yaml"
        )
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(f"{source} must declare version: 1")
    if not isinstance(data.get("suites"), dict):
        raise ValueError(f"{source} must define a suites mapping")
    return data


def expand_suites(manifest: dict[str, Any]) -> dict[str, list[EvalCase]]:
    """Expand every suite matrix into concrete cases, deterministically ordered."""
    expanded: dict[str, list[EvalCase]] = {}
    for suite_id, suite in manifest["suites"].items():
        if not isinstance(suite, dict):
            raise ValueError(f"suite {suite_id!r} must be a mapping")
        cases: list[EvalCase] = []
        for entry in suite.get("cases", []):
            for variant in entry["variants"]:
                expected = entry["expected_outcome"]
                if expected not in {"resolved", "reopened", "failed"}:
                    raise ValueError(f"suite {suite_id}: unsupported expected_outcome {expected!r}")
                cases.append(
                    EvalCase(
                        id=f"{entry['scenario']}-v{variant}"
                        + ("-relapse" if entry.get("relapse") else "")
                        + ("-injection" if entry.get("injection") else ""),
                        suite=suite_id,
                        scenario=entry["scenario"],
                        variant=int(variant),
                        expected_outcome=expected,
                        expect_approval=bool(entry["expect_approval"]),
                        expected_actions=tuple(entry["expected_actions"]),
                        relapse=bool(entry.get("relapse", False)),
                        injection=bool(entry.get("injection", False)),
                        stability_seconds=int(suite.get("stability_seconds", 0)),
                    )
                )
        expanded[suite_id] = cases
    return expanded


def load_catalog(
    suites: list[str] | None = None,
    *,
    manifest_path: Path | None = None,
) -> list[EvalCase]:
    """Return cases for the requested suites in manifest order."""
    expanded = expand_suites(load_manifest(manifest_path))
    if suites is None:
        return [case for suite_cases in expanded.values() for case in suite_cases]
    requested = [suite if suite in expanded else f"suite {suite!r}" for suite in suites]
    unknown = [name for name in requested if name.startswith("suite ")]
    if unknown:
        available = ", ".join(sorted(expanded))
        raise KeyError(f"unknown suite(s) {unknown}; available: {available}")
    return [case for suite in suites for case in expanded[suite]]


def suite_case_counts(manifest_path: Path | None = None) -> dict[str, int]:
    return {
        suite: len(cases) for suite, cases in expand_suites(load_manifest(manifest_path)).items()
    }


__all__ = [
    "MANIFEST_NAME",
    "default_manifest_path",
    "expand_suites",
    "load_catalog",
    "load_manifest",
    "suite_case_counts",
]
