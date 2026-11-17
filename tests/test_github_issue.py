"""Opening an issue must not open the same issue twice.

The idempotency key stops one plan from executing twice. This covers the other
case: a second plan, or a human re-running the fix, against a repository that
already has the ticket. GitHub's issue list is the only way to know, so the
connector reads before it writes.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from nightjar.connectors.github import GitHubConnector
from nightjar.core.enums import ActionType, RiskClass
from nightjar.core.models import ActionSpec

BASE = "https://api.github.com"
TOKEN = "ghp_" + "a" * 36
REPOSITORY = "acme/shop"
TITLE = "Checkout returns 502 after deploy 4f2c1"


def issue_action(**params: object) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.OPEN_ISSUE,
        target=REPOSITORY,
        risk=RiskClass.R1,
        params={"title": TITLE, "body": "Nightjar case file", **params},
    )


@pytest.fixture
def connector() -> GitHubConnector:
    return GitHubConnector(TOKEN, base_url=BASE)


@respx.mock
async def test_a_new_issue_is_created_when_none_exists(connector: GitHubConnector) -> None:
    respx.get(f"{BASE}/repos/{REPOSITORY}/issues").mock(return_value=Response(200, json=[]))
    created = respx.post(f"{BASE}/repos/{REPOSITORY}/issues").mock(
        return_value=Response(
            201, json={"html_url": "https://github.com/acme/shop/issues/7", "number": 7}
        )
    )

    result = await connector.execute(issue_action())

    assert created.called
    assert result == {
        "issue_url": "https://github.com/acme/shop/issues/7",
        "number": 7,
        "reused": False,
    }


@respx.mock
async def test_an_existing_open_issue_is_reused(connector: GitHubConnector) -> None:
    respx.get(f"{BASE}/repos/{REPOSITORY}/issues").mock(
        return_value=Response(
            200,
            json=[
                {"title": "Something else", "number": 1, "html_url": "https://x/1"},
                {"title": TITLE, "number": 42, "html_url": "https://x/42"},
            ],
        )
    )
    created = respx.post(f"{BASE}/repos/{REPOSITORY}/issues").mock(
        return_value=Response(201, json={"number": 99})
    )

    result = await connector.execute(issue_action())

    assert not created.called
    assert result == {"issue_url": "https://x/42", "number": 42, "reused": True}


@respx.mock
async def test_a_pull_request_with_the_same_title_is_not_an_issue(
    connector: GitHubConnector,
) -> None:
    """GitHub returns PRs from the issues endpoint; reusing one would be wrong."""
    respx.get(f"{BASE}/repos/{REPOSITORY}/issues").mock(
        return_value=Response(
            200,
            json=[
                {
                    "title": TITLE,
                    "number": 12,
                    "html_url": "https://x/12",
                    "pull_request": {"url": "https://api.github.com/repos/acme/shop/pulls/12"},
                }
            ],
        )
    )
    created = respx.post(f"{BASE}/repos/{REPOSITORY}/issues").mock(
        return_value=Response(201, json={"number": 13, "html_url": "https://x/13"})
    )

    result = await connector.execute(issue_action())

    assert created.called
    assert result["number"] == 13
    assert result["reused"] is False


@respx.mock
async def test_a_refused_listing_does_not_block_the_incident(
    connector: GitHubConnector,
) -> None:
    """Best effort: the read is a duplicate check, not a precondition."""
    respx.get(f"{BASE}/repos/{REPOSITORY}/issues").mock(
        return_value=Response(403, json={"message": "Resource not accessible"})
    )
    created = respx.post(f"{BASE}/repos/{REPOSITORY}/issues").mock(
        return_value=Response(201, json={"number": 5, "html_url": "https://x/5"})
    )

    result = await connector.execute(issue_action())

    assert created.called
    assert result["number"] == 5


@respx.mock
async def test_the_title_comes_from_the_action_not_a_constant(
    connector: GitHubConnector,
) -> None:
    respx.get(f"{BASE}/repos/{REPOSITORY}/issues").mock(return_value=Response(200, json=[]))
    created = respx.post(f"{BASE}/repos/{REPOSITORY}/issues").mock(
        return_value=Response(201, json={"number": 8})
    )

    await connector.execute(issue_action(title="Custom title"))

    body = created.calls[0].request.content.decode()
    assert "Custom title" in body
