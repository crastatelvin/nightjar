from __future__ import annotations

import json
from pathlib import Path

import pytest

from nightjar.evals.baseline import (
    baseline_path,
    baseline_slug,
    build_baseline,
    compare_baseline,
    load_baseline,
    write_baseline,
)
from nightjar.evals.models import CaseScore
from nightjar.evals.report import GateThresholds, evaluate_gates
from nightjar.evals.runner import summarize


def score(case_id: str = "cache-v0", **overrides: object) -> CaseScore:
    values: dict[str, object] = {
        "case_id": case_id,
        "suite": "cornershop-30",
        "scenario": "site_cache_poisoned",
        "variant": 0,
        "expected_outcome": "resolved",
        "status": "resolved",
        "outcome_correct": True,
        "false_resolution": False,
        "verified": True,
        "expected_actions": ("purge_cache",),
        "executed_actions": ("purge_cache",),
        "action_match": True,
        "approvals_expected": False,
        "approvals_granted": 0,
        "approvals_ok": True,
        "injection": False,
        "injection_present": False,
        "injection_resistant": True,
        "chain_valid": True,
        "manual_actions_visible": True,
    }
    values.update(overrides)
    return CaseScore(**values)  # type: ignore[arg-type]


def summary(*scores: CaseScore):
    return summarize(list(scores), provider="mock", model="rules-v1", suite="cornershop-30")


def test_model_names_become_safe_filenames() -> None:
    assert baseline_slug("openai", "gpt-5.4-mini") == "openai-gpt-5-4-mini"
    assert baseline_slug("google", "gemini/2.5:pro") == "google-gemini-2-5-pro"
    assert baseline_path(Path("baselines"), "anthropic", "claude-sonnet-4").name == (
        "anthropic-claude-sonnet-4.json"
    )


def test_a_baseline_records_every_case_and_the_headline_metrics(tmp_path) -> None:
    first = summary(score("a"), score("b", status="failed", outcome_correct=False))
    baseline = build_baseline(first)
    path = write_baseline(tmp_path / "mock-rules-v1.json", baseline)

    loaded = load_baseline(path)
    assert loaded is not None
    assert loaded["provider"] == "mock"
    assert loaded["model"] == "rules-v1"
    assert [case["case_id"] for case in loaded["cases"]] == ["a", "b"]
    assert loaded["metrics"]["case_count"] == 2
    assert loaded["metrics"]["accuracy"] == 0.5

    # The file is meant to be reviewed in a diff, so it is stable JSON.
    assert json.loads(path.read_text())["schema_version"] == 1


def test_compare_reports_only_fields_that_got_worse() -> None:
    before = build_baseline(summary(score("a"), score("b")))
    after = summary(
        score("a", verified=False),
        score("b"),  # unchanged
        score("c"),  # new case, not in the baseline
    )

    regressions = compare_baseline(before, after)

    assert [item.describe() for item in regressions] == ["a: verified True -> False"]


def test_compare_ignores_improvements_and_missing_cases() -> None:
    before = build_baseline(summary(score("a", status="failed", outcome_correct=False), score("b")))
    after = summary(score("a"))  # b is absent from this run

    assert compare_baseline(before, after) == []


def test_a_newly_unbound_action_is_a_regression() -> None:
    before = build_baseline(summary(score("a")))
    after = summary(score("a", unavailable_actions=("purge_cache",)))

    regressions = compare_baseline(before, after)

    assert len(regressions) == 1
    assert regressions[0].field == "unavailable_actions"
    assert regressions[0].after == ["purge_cache"]


def test_a_missing_or_unknown_baseline_is_not_compared(tmp_path) -> None:
    assert load_baseline(tmp_path / "missing.json") is None
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"schema_version": 0, "cases": []}))
    assert load_baseline(path) is None


def test_the_baseline_gate_fails_on_a_regression() -> None:
    gates = evaluate_gates(summary(score("a")), GateThresholds(), baseline_regressions=1)
    baseline_gate = next(gate for gate in gates if gate["name"] == "baseline_regressions")

    assert baseline_gate["passed"] is False
    assert baseline_gate["observed"] == 1


def test_recording_and_ignoring_a_baseline_are_mutually_exclusive() -> None:
    from nightjar.evals.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["--record"]).record is True
    assert parser.parse_args(["--no-baseline"]).no_baseline is True
    with pytest.raises(SystemExit):
        parser.parse_args(["--record", "--no-baseline"])
