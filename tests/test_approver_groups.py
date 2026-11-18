"""Approver groups: the identity provider decides who may sign off.

Separation of duties answers "the person who filed it cannot approve it". It does
not answer "may this person approve anything at all". That is a directory
question, and the directory already knows: the token carries group membership.

Configuring groups turns "holds a credential" into "is in the approval group". A
shared token has no group claim, so it is refused rather than allowed to bypass
the policy it cannot satisfy.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from nightjar.api.auth import AuthRejected, Identity
from nightjar.api.deps import require_approver
from nightjar.api.main import create_app
from nightjar.api.settings import Settings
from tests.support import scenario_payload, wait_for_status

GOOD = "good-token"
APPROVERS = "nightjar-approvers"
OPERATORS = "nightjar-operators"


class StubVerifier:
    """Stands in for the issuer; the real verifier has its own tests."""

    def __init__(self, identity: Identity) -> None:
        self.identity = identity

    async def verify(self, token: str) -> Identity:
        if token != GOOD:
            raise AuthRejected("token rejected: bad signature")
        return self.identity


def identity_in(*groups: str) -> Identity:
    return Identity(
        subject="auth0|bob",
        method="oidc",
        email="bob@acme.example",
        groups=groups,
    )


def client_for(
    settings: Settings,
    identity: Identity | None,
    *,
    groups: list[str],
    token: str | None = None,
) -> TestClient:
    tuned = settings.model_copy(update={"api_token": token, "oidc_approver_groups": groups})
    app = create_app(tuned)
    if identity is not None:
        app.state.oidc = StubVerifier(identity)
    credential = token or GOOD
    return TestClient(app, headers={"authorization": f"Bearer {credential}"})


def pending_approval(client: TestClient, lab) -> tuple[str, dict[str, object], str]:
    payload = scenario_payload(lab, "credential_leaked")
    created = client.post("/v1/incidents", params={"wait": "true"}, json=payload)
    assert created.status_code == 201, created.text
    incident = created.json()
    case = wait_for_status(client, incident["id"], {"awaiting_approval"})
    return incident["id"], case["approvals"][0], str(payload["metadata"]["credential_id"])


def test_a_member_of_the_approver_group_can_sign_off(settings: Settings, lab) -> None:
    client = client_for(settings, identity_in(APPROVERS, OPERATORS), groups=[APPROVERS])
    with client:
        incident_id, approval, _credential_id = pending_approval(client, lab)
        response = client.post(
            f"/v1/incidents/{incident_id}/approvals/{approval['id']}",
            json={"approved": True, "comment": "ok"},
        )

        assert response.status_code == 200, response.text
        resolved = wait_for_status(client, incident_id, {"resolved"})
        assert resolved["incident"]["status"] == "resolved"

    assert lab.get("/lab/audit").json()["healthy"] is True


def test_a_caller_outside_the_group_is_refused_and_nothing_runs(settings: Settings, lab) -> None:
    client = client_for(settings, identity_in(OPERATORS), groups=[APPROVERS])
    with client:
        incident_id, approval, credential_id = pending_approval(client, lab)
        response = client.post(
            f"/v1/incidents/{incident_id}/approvals/{approval['id']}",
            json={"approved": True, "comment": "ok"},
        )

        assert response.status_code == 403
        assert APPROVERS in response.json()["detail"]

        case = client.get(f"/v1/incidents/{incident_id}").json()
        assert case["approvals"][0]["status"] == "pending"
        assert case["incident"]["status"] == "awaiting_approval"
        # The refusal executed nothing: the leaked key is still live in the lab.
        credential = lab.get(f"/lab/credentials/{credential_id}").json()
        assert credential["status"] == "active"


def test_the_shared_token_cannot_bypass_approver_groups(settings: Settings, lab) -> None:
    """A static secret cannot prove group membership, so it cannot approve."""
    client = client_for(settings, None, groups=[APPROVERS], token="s3cret-token")
    with client:
        incident_id, approval, _credential_id = pending_approval(client, lab)
        response = client.post(
            f"/v1/incidents/{incident_id}/approvals/{approval['id']}",
            json={"approved": True, "comment": "ok"},
        )

    assert response.status_code == 403
    assert "signed off by a member" in response.json()["detail"]


def test_no_configured_groups_keeps_any_authenticated_caller_able_to_approve(
    settings: Settings, lab
) -> None:
    client = client_for(settings, identity_in(OPERATORS), groups=[])
    with client:
        incident_id, approval, _credential_id = pending_approval(client, lab)
        response = client.post(
            f"/v1/incidents/{incident_id}/approvals/{approval['id']}",
            json={"approved": True, "comment": "ok"},
        )

    assert response.status_code == 200, response.text


def request_with(settings: Settings, identity: Identity | None) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
        state=SimpleNamespace(identity=identity),
    )


async def test_the_dependency_is_a_no_op_without_configured_groups(
    settings: Settings,
) -> None:
    tuned = settings.model_copy(update={"oidc_approver_groups": []})
    await require_approver(request_with(tuned, None))


async def test_the_dependency_refuses_an_unverified_identity(settings: Settings) -> None:
    tuned = settings.model_copy(update={"oidc_approver_groups": [APPROVERS]})
    with pytest.raises(HTTPException) as excinfo:
        await require_approver(request_with(tuned, None))
    assert excinfo.value.status_code == 403


async def test_the_dependency_refuses_a_shared_token(settings: Settings) -> None:
    tuned = settings.model_copy(update={"oidc_approver_groups": [APPROVERS]})
    shared = Identity(subject="token", method="token")
    with pytest.raises(HTTPException) as excinfo:
        await require_approver(request_with(tuned, shared))
    assert excinfo.value.status_code == 403


async def test_the_dependency_accepts_a_group_member(settings: Settings) -> None:
    tuned = settings.model_copy(update={"oidc_approver_groups": [APPROVERS]})
    await require_approver(request_with(tuned, identity_in(OPERATORS, APPROVERS)))


def configured(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "oidc_issuer": "https://id.example.com",
        "oidc_audience": "nightjar",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_approver_groups_parse_both_env_forms() -> None:
    assert configured(oidc_approver_groups="a,b").oidc_approver_groups == ["a", "b"]
    assert configured(oidc_approver_groups='["a", "b"]').oidc_approver_groups == ["a", "b"]
    assert configured(oidc_approver_groups="").oidc_approver_groups == []


def test_approver_groups_without_oidc_are_refused() -> None:
    """Nobody could ever approve: the shared token has no groups to check."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, oidc_approver_groups="nightjar-approvers")
    assert "NIGHTJAR_OIDC_APPROVER_GROUPS" in str(excinfo.value)


def test_group_matching_is_exact() -> None:
    """A claim value is compared as given: no prefix, case, or DN guessing."""
    identity = identity_in("Nightjar-Approvers")
    assert not identity.in_group([APPROVERS])
    assert identity.in_group(["Nightjar-Approvers"])
