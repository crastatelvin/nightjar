"""The documentation is part of the product, so its links have to resolve.

An operator reading a runbook at 03:00 should never hit a 404 on the page that
explains what to do next. This walks every relative link and anchor in the docs and
the README and fails when one does not point at a file or a heading that exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)


def _pages() -> list[Path]:
    pages = [ROOT / "README.md", *sorted(DOCS.rglob("*.md"))]
    return [page for page in pages if page.exists()]


def _slug(heading: str) -> str:
    """GitHub's heading anchor for the subset of headings this project writes."""
    text = heading.strip().strip("#").strip()
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text).strip("-")


def _anchors(page: Path) -> set[str]:
    body = FENCE.sub("", page.read_text())
    return {_slug(match.group(2)) for match in HEADING.finditer(body)}


def _links(page: Path) -> list[str]:
    body = FENCE.sub("", page.read_text())
    return [match.group(1) for match in LINK.finditer(body)]


@pytest.mark.parametrize("page", _pages(), ids=lambda page: str(page.relative_to(ROOT)))
def test_every_relative_link_resolves(page: Path) -> None:
    for target in _links(page):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        destination = (page.parent / path).resolve() if path else page
        assert destination.exists(), f"{page.relative_to(ROOT)} links to missing {target}"
        if anchor:
            assert anchor in _anchors(destination), (
                f"{page.relative_to(ROOT)} links to {target}, "
                f"which is not a heading in {destination.relative_to(ROOT)}"
            )


def test_the_runbook_index_lists_every_runbook() -> None:
    directory = DOCS / "runbooks"
    index = (directory / "README.md").read_text()
    for runbook in sorted(directory.glob("*.md")):
        if runbook.name == "README.md":
            continue
        assert runbook.name in index, f"{runbook.name} is not linked from the runbook index"


def test_the_readme_and_operations_doc_point_at_the_runbooks() -> None:
    assert "../runbooks/README.md" not in (ROOT / "README.md").read_text()
    assert "docs/runbooks/" in (ROOT / "README.md").read_text()
    assert "runbooks/" in (DOCS / "operations.md").read_text()
