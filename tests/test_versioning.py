"""One version, written down once and checked everywhere.

The tag, the wheel metadata, `nightjar.__version__`, and the changelog all claim to
describe the same release. A mismatch is not cosmetic: `pip install
nightjar-ir==0.1.1` would get a package that reports 0.1.0, and the changelog entry
an operator reads would describe a different build.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import nightjar

ROOT = Path(__file__).resolve().parent.parent


def _project() -> dict[str, object]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def test_the_distribution_name_is_the_one_on_pypi() -> None:
    # The import package stays `nightjar`; the distribution is `nightjar-ir`
    # because the short name is taken.
    assert _project()["name"] == "nightjar-ir"


def test_the_declared_version_matches_the_importable_package() -> None:
    assert _project()["version"] == nightjar.__version__


def test_the_changelog_documents_the_declared_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert f"## [{nightjar.__version__}]" in changelog


def test_the_console_scripts_are_declared_once() -> None:
    scripts = _project()["scripts"]
    assert isinstance(scripts, dict)
    assert set(scripts) == {
        "nightjar-api",
        "nightjar-worker",
        "nightjar-lab",
        "nightjar-eval",
        "nightjarctl",
    }
