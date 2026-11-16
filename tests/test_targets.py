from __future__ import annotations

import pytest

from nightjar.core.enums import ActionType, RiskClass
from nightjar.core.models import ActionSpec
from nightjar.core.targets import (
    normalize_managed_targets,
    resolve_target,
    target_allowed,
)


def purge(**params: object) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.PURGE_CACHE,
        target="cornershop",
        params={"zone": "cornershop", "urls": ["https://shop.example"], **params},
        risk=RiskClass.R1,
    )


def test_zone_is_resolved_from_params() -> None:
    target = resolve_target(purge())
    assert target is not None
    assert (target.provider, target.kind, target.value) == (
        "cloudflare",
        "zones",
        "cornershop",
    )


def test_an_explicit_connector_wins() -> None:
    target = resolve_target(purge(connector="lab"))
    assert target is not None
    assert target.provider == "lab"


def test_the_action_target_is_the_fallback() -> None:
    action = ActionSpec(
        action_type=ActionType.ENABLE_UNDER_ATTACK,
        target="zone-abc",
        params={},
        risk=RiskClass.R2,
    )
    target = resolve_target(action)
    assert target is not None
    assert target.value == "zone-abc"


def test_a_repository_is_lowercased_for_comparison() -> None:
    action = ActionSpec(
        action_type=ActionType.OPEN_ISSUE,
        target="Acme/Site",
        params={"repository": "Acme/Site"},
        risk=RiskClass.R1,
    )
    target = resolve_target(action)
    assert target is not None
    assert target.value == "acme/site"


def test_a_provider_scoped_action_has_no_destination() -> None:
    action = ActionSpec(
        action_type=ActionType.REVOKE_CREDENTIAL,
        target="github",
        params={"credential_ref": "env:EXPOSED_PAT"},
        risk=RiskClass.R2,
    )
    target = resolve_target(action)
    assert target is not None
    assert target.kind is None


def test_unknown_actions_have_no_target() -> None:
    action = ActionSpec(
        action_type=ActionType.NOTIFY,
        target="webhook",
        params={"message": "hi"},
        risk=RiskClass.R0,
    )
    target = resolve_target(action)
    assert target is not None
    assert target.provider == "webhook"


def test_an_empty_allowlist_is_unmanaged_development_mode() -> None:
    assert target_allowed(resolve_target(purge()), {}) is True


def test_a_provider_must_be_managed() -> None:
    managed = normalize_managed_targets({"cloudflare": {"zones": ["cornershop"]}})
    assert target_allowed(resolve_target(purge()), managed) is True
    assert target_allowed(resolve_target(purge(connector="lab")), managed) is False


def test_a_destination_must_be_listed() -> None:
    managed = normalize_managed_targets({"cloudflare": {"zones": ["another-zone"]}})
    assert target_allowed(resolve_target(purge()), managed) is False


def test_a_missing_list_denies() -> None:
    managed = normalize_managed_targets({"cloudflare": {"accounts": ["acct"]}})
    assert target_allowed(resolve_target(purge()), managed) is False


def test_an_empty_destination_denies() -> None:
    action = ActionSpec(
        action_type=ActionType.PURGE_CACHE,
        target="",
        params={},
        risk=RiskClass.R1,
    )
    managed = normalize_managed_targets({"cloudflare": {"zones": ["*"]}})
    assert target_allowed(resolve_target(action), managed) is False


def test_a_wildcard_manages_every_destination_for_that_kind() -> None:
    managed = normalize_managed_targets({"cloudflare": {"zones": ["*"]}})
    assert target_allowed(resolve_target(purge()), managed) is True


def test_provider_only_management_allows_revocation() -> None:
    action = ActionSpec(
        action_type=ActionType.REVOKE_CREDENTIAL,
        target="github",
        params={"credential_ref": "env:EXPOSED_PAT"},
        risk=RiskClass.R2,
    )
    managed = normalize_managed_targets({"github": {}})
    assert target_allowed(resolve_target(action), managed) is True


def test_repository_matching_ignores_case_on_both_sides() -> None:
    action = ActionSpec(
        action_type=ActionType.OPEN_ISSUE,
        target="Acme/Site",
        params={"repository": "Acme/Site"},
        risk=RiskClass.R1,
    )
    managed = normalize_managed_targets({"github": {"repos": ["ACME/SITE"]}})
    assert target_allowed(resolve_target(action), managed) is True


def test_a_bare_string_is_accepted_as_a_single_target() -> None:
    managed = normalize_managed_targets({"cloudflare": {"zones": "cornershop"}})
    assert managed["cloudflare"]["zones"] == ["cornershop"]


def test_a_provider_entry_must_be_an_object() -> None:
    with pytest.raises(ValueError, match="must map to a JSON object"):
        normalize_managed_targets({"cloudflare": ["cornershop"]})


def test_a_target_list_must_be_strings() -> None:
    with pytest.raises(ValueError, match="list of strings"):
        normalize_managed_targets({"cloudflare": {"zones": [1, 2]}})


def test_blank_entries_are_dropped_rather_than_matching_everything() -> None:
    managed = normalize_managed_targets({"cloudflare": {"zones": ["  "]}})
    assert managed["cloudflare"]["zones"] == []
    assert target_allowed(resolve_target(purge()), managed) is False


def test_a_mail_action_targets_the_mailbox_not_the_message() -> None:
    action = ActionSpec(
        action_type=ActionType.QUARANTINE_MESSAGE,
        target="msg_phish_0001",
        params={"message_id": "msg_phish_0001"},
        risk=RiskClass.R1,
    )
    target = resolve_target(action)
    assert target is not None
    assert (target.provider, target.kind, target.value) == ("google", "mailboxes", "")
    managed = normalize_managed_targets({"google": {"mailboxes": ["owner@example.com"]}})
    assert target_allowed(target, managed) is False


def test_a_mail_action_with_a_mailbox_is_managed() -> None:
    action = ActionSpec(
        action_type=ActionType.BLOCK_SENDER,
        target="bad@example.com",
        params={"sender": "bad@example.com", "mailbox": "Owner@Example.com"},
        risk=RiskClass.R2,
    )
    managed = normalize_managed_targets({"google": {"mailboxes": ["owner@example.com"]}})
    assert target_allowed(resolve_target(action), managed) is True


def test_render_uses_service_vocabulary_not_project() -> None:
    action = ActionSpec(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        target="dep-1",
        params={"connector": "render", "service": "srv-1", "target_version": "dep-1"},
        risk=RiskClass.R2,
    )
    target = resolve_target(action)
    assert target is not None
    assert (target.provider, target.kind, target.value) == ("render", "services", "srv-1")

    managed = normalize_managed_targets({"render": {"services": ["srv-1"]}})
    assert target_allowed(target, managed) is True


def test_fly_restart_uses_app_vocabulary() -> None:
    action = ActionSpec(
        action_type=ActionType.RESTART_SERVICE,
        target="app-1",
        params={"connector": "fly", "app": "app-1"},
        risk=RiskClass.R2,
    )
    target = resolve_target(action)
    assert target is not None
    assert (target.provider, target.kind, target.value) == ("fly", "apps", "app-1")


def test_cloudflare_and_vercel_rollback_still_use_projects() -> None:
    action = ActionSpec(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        target="dpl-1",
        params={"connector": "vercel", "project": "prj-1", "target_version": "dpl-1"},
        risk=RiskClass.R2,
    )
    target = resolve_target(action)
    assert target is not None
    assert (target.provider, target.kind, target.value) == ("vercel", "projects", "prj-1")


def test_a_microsoft_mail_action_is_managed_by_mailbox() -> None:
    action = ActionSpec(
        action_type=ActionType.QUARANTINE_MESSAGE,
        target="msg-1",
        params={
            "connector": "microsoft",
            "mailbox": "Owner@Acme.Example",
            "message_id": "msg-1",
        },
        risk=RiskClass.R1,
    )
    target = resolve_target(action)
    assert target is not None
    assert (target.provider, target.kind, target.value) == (
        "microsoft",
        "mailboxes",
        "owner@acme.example",
    )


def test_a_microsoft_mail_action_never_falls_back_to_the_message_id() -> None:
    action = ActionSpec(
        action_type=ActionType.QUARANTINE_MESSAGE,
        target="msg-1",
        params={"connector": "microsoft", "message_id": "msg-1"},
        risk=RiskClass.R1,
    )
    managed = normalize_managed_targets({"microsoft": {"mailboxes": ["owner@acme.example"]}})
    assert target_allowed(resolve_target(action), managed) is False


def test_microsoft_session_revocation_is_managed_by_the_user() -> None:
    action = ActionSpec(
        action_type=ActionType.REVOKE_CREDENTIAL,
        target="cred-1",
        params={"connector": "microsoft", "upn": "owner@acme.example"},
        risk=RiskClass.R2,
    )
    managed = normalize_managed_targets({"microsoft": {"mailboxes": ["owner@acme.example"]}})
    target = resolve_target(action)
    assert target is not None
    assert (target.kind, target.value) == ("mailboxes", "owner@acme.example")
    assert target_allowed(target, managed) is True
