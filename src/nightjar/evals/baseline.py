"""Recorded evaluation baselines, and the comparison that makes them useful.

A baseline is not a screenshot of a good day. It is the per-case evidence that a
provider used to produce, kept so a later run can say *which* case got worse
instead of only that the aggregate moved. Latency and cost are deliberately not
compared: they measure the host and the provider's current price, not whether
Nightjar still closes incidents honestly.

The file is plain JSON so a reviewer can diff it in a pull request. It records
the provider and model it belongs to, and it is only consulted when both match
the current run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nightjar import __version__
from nightjar.evals.models import EvalSummary

SCHEMA_VERSION = 1
CASE_FIELDS = (
    "outcome_correct",
    "false_resolution",
    "verified",
    "action_match",
    "approvals_ok",
    "injection_resistant",
    "chain_valid",
    "manual_actions_visible",
)


def baseline_slug(provider: str, model: str) -> str:
    """A stable filename for a provider/model pair.

    Model names contain ``/``, ``:`` and dots; a filename that keeps them is
    awkward on Windows and impossible to cite in a shell without quoting.
    """
    raw = f"{provider}-{model}".lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-") or "unknown"


def baseline_path(directory: Path, provider: str, model: str) -> Path:
    return directory / f"{baseline_slug(provider, model)}.json"


@dataclass(frozen=True)
class Regression:
    case_id: str
    field: str
    before: Any
    after: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "field": self.field,
            "before": self.before,
            "after": self.after,
        }

    def describe(self) -> str:
        return f"{self.case_id}: {self.field} {self.before!r} -> {self.after!r}"


def build_baseline(summary: EvalSummary, *, recorded_at: datetime | None = None) -> dict[str, Any]:
    moment = recorded_at or datetime.now(UTC)
    return {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": moment.isoformat(),
        "nightjar_version": __version__,
        "provider": summary.provider,
        "model": summary.model,
        "suite": summary.suite,
        "metrics": {
            "case_count": summary.case_count,
            "accuracy": summary.accuracy,
            "false_resolution_rate": summary.false_resolution_rate,
            "policy_violations": summary.policy_violations,
            "injection_resistance_rate": summary.injection_resistance_rate,
            "chain_integrity_rate": summary.chain_integrity_rate,
            "action_match_rate": summary.action_match_rate,
            "approval_rate": summary.approval_rate,
            "unavailable_action_rate": summary.unavailable_action_rate,
            "manual_step_visibility": summary.manual_step_visibility,
        },
        "cases": [
            {
                "case_id": score.case_id,
                **{field: getattr(score, field) for field in CASE_FIELDS},
                "unavailable_actions": list(score.unavailable_actions),
            }
            for score in summary.cases
        ],
    }


def write_baseline(path: Path, baseline: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def load_baseline(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError, json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return None
    return data


def compare_baseline(baseline: dict[str, Any], summary: EvalSummary) -> list[Regression]:
    """Report every case that was better in the recorded run.

    Only cases present in both runs are compared. A suite that grew or shrank is
    not a regression, and a provider/model mismatch means the baseline is for a
    different thing; the caller decides whether to compare at all.
    """
    before = {
        str(case.get("case_id")): case
        for case in baseline.get("cases") or []
        if isinstance(case, dict)
    }
    regressions: list[Regression] = []
    for score in summary.cases:
        recorded = before.get(score.case_id)
        if recorded is None:
            continue
        for field in CASE_FIELDS:
            old = bool(recorded.get(field))
            new = bool(getattr(score, field))
            if old and not new:
                regressions.append(
                    Regression(case_id=score.case_id, field=field, before=old, after=new)
                )
        if recorded.get("unavailable_actions"):
            # A baseline that was already dirty cannot regress on this field;
            # the aggregate gate still has to be clean.
            continue
        if score.unavailable_actions:
            regressions.append(
                Regression(
                    case_id=score.case_id,
                    field="unavailable_actions",
                    before=[],
                    after=list(score.unavailable_actions),
                )
            )
    return regressions


__all__ = [
    "CASE_FIELDS",
    "SCHEMA_VERSION",
    "Regression",
    "baseline_path",
    "baseline_slug",
    "build_baseline",
    "compare_baseline",
    "load_baseline",
    "write_baseline",
]
