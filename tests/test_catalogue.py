from __future__ import annotations

from pathlib import Path

import pytest

from nightjar.api.settings import Settings
from nightjar.cli.main import main
from nightjar.connectors.registry import build_registry
from nightjar.core.capabilities import ActionCapability, assess
from nightjar.core.catalogue import (
    ACTION_LABELS,
    BEGIN,
    END,
    PROVIDER_NAMES,
    extract_block,
    render_block,
    render_table,
    replace_block,
)
from nightjar.core.enums import ActionType

README = Path(__file__).resolve().parents[1] / "README.md"


def catalogue() -> list[ActionCapability]:
    settings = Settings()
    registry = build_registry(settings, include_unconfigured=True)
    report = assess(
        registry.connectors,
        [],
        environment=settings.environment,
        probed=False,
    )
    return report.actions


def test_every_advertised_action_states_its_proof() -> None:
    missing = [
        f"{action.provider}:{action.action_type.value}"
        for action in catalogue()
        if not action.proof.strip()
    ]

    assert missing == []


def test_every_provider_and_action_has_a_human_name() -> None:
    actions = catalogue()

    assert {action.provider for action in actions} <= set(PROVIDER_NAMES)
    assert {action.action_type for action in actions} <= set(ACTION_LABELS)


def test_the_table_says_which_actions_a_human_performs() -> None:
    table = render_table(catalogue())
    stripe = next(line for line in table.splitlines() if line.startswith("| **Stripe**"))

    assert "| manual |" in stripe
    assert "Basic auth" in stripe
    assert "| **GitHub** | revoke token | API |" in table


def test_the_table_is_rendered_from_the_api_catalogue() -> None:
    settings = Settings()
    registry = build_registry(settings, include_unconfigured=True)
    report = assess(registry.connectors, [], environment=settings.environment, probed=False)

    table = render_table(report.actions)

    assert table.splitlines()[2].startswith("| **Cloudflare** |")
    assert table.count("| **Lab** |") == len(
        [action for action in report.actions if action.provider == "lab"]
    )
    assert report.as_dict()["actions"][0]["proof"]  # type: ignore[index]


def test_a_capability_without_a_proof_cannot_be_documented() -> None:
    with pytest.raises(ValueError, match="without a proof"):
        render_table(
            [
                ActionCapability(
                    action_type=ActionType.PURGE_CACHE,
                    provider="cloudflare",
                    automated=True,
                    target_kinds=("zone",),
                    notes="",
                    verdict="configured",
                )
            ]
        )


def test_the_readme_table_matches_the_connectors() -> None:
    text = README.read_text(encoding="utf-8")

    assert extract_block(text) == render_block(catalogue())


def test_check_mode_fails_on_drift_and_write_repairs_it(tmp_path: Path) -> None:
    page = tmp_path / "README.md"
    page.write_text(f"intro\n\n{BEGIN}\nstale\n{END}\n\noutro\n", encoding="utf-8")

    assert main(["capabilities", "--check", str(page)]) == 1
    assert main(["capabilities", "--write", str(page)]) == 0
    assert main(["capabilities", "--check", str(page)]) == 0

    repaired = page.read_text(encoding="utf-8")
    assert repaired.startswith("intro\n\n")
    assert repaired.endswith("\n\noutro\n")
    assert "| **Stripe** | revoke token | manual |" in repaired


def test_check_mode_refuses_a_page_without_markers(tmp_path: Path) -> None:
    page = tmp_path / "README.md"
    page.write_text("no markers here\n", encoding="utf-8")

    assert main(["capabilities", "--check", str(page)]) == 1


def test_replace_requires_the_markers() -> None:
    with pytest.raises(ValueError, match="missing"):
        extract_block("plain page")
    with pytest.raises(ValueError, match="missing"):
        replace_block("plain page", render_block(catalogue()))
