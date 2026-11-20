"""Where a lab seed says the site lives.

The lab is called from two places that disagree about its address: a test or a
script on the host uses `127.0.0.1:8100`, the API container in Compose uses
`http://lab:8100`, and inside that container `127.0.0.1:8100` is the API itself.
A seed carrying the caller's address therefore points the verifier at nothing,
which is a real outage look-alike that no amount of retrying fixes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from nightjar.lab.cornershop.app import app


def _seed(client: TestClient) -> dict[str, object]:
    response = client.post("/lab/scenario/site_cache_poisoned")
    assert response.status_code == 200
    return response.json()


def test_a_seed_defaults_to_the_address_the_caller_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NIGHTJAR_LAB_PUBLIC_URL", raising=False)
    with TestClient(app) as client:
        seed = _seed(client)

    metadata = seed["metadata"]
    assert isinstance(metadata, dict)
    assert str(metadata["site_url"]).startswith("http://testserver/")


def test_a_seed_can_name_the_address_the_api_will_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIGHTJAR_LAB_PUBLIC_URL", "http://lab:8100/")
    with TestClient(app) as client:
        seed = _seed(client)

    metadata = seed["metadata"]
    assert isinstance(metadata, dict)
    # Trailing slash is normalised: "http://lab:8100/site/", never "//site/".
    assert metadata["site_url"] == "http://lab:8100/site/"
    assert seed["public_base_url"] == "http://lab:8100"
