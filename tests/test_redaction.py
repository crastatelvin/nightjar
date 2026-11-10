from __future__ import annotations

from nightjar.core.redaction import (
    mask_secret,
    redact_obj,
    redact_pii,
    redact_secrets,
    redact_secrets_obj,
    redact_text,
)

STRIPE = "sk_live_" + "a1B2c3D4e5F6g7H8i9J0"
GITHUB = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
GOOGLE = "AIza" + "B" * 35
PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234567890\n-----END RSA PRIVATE KEY-----"
)


def test_stripe_key_is_redacted() -> None:
    cleaned, kinds = redact_text(f"leaked key {STRIPE} in the repo")
    assert STRIPE not in cleaned
    assert "[REDACTED:stripe]" in cleaned
    assert kinds == ["stripe"]


def test_github_aws_and_google_keys_are_redacted() -> None:
    cleaned, kinds = redact_text(f"{GITHUB} {AWS} {GOOGLE}")
    assert GITHUB not in cleaned
    assert AWS not in cleaned
    assert GOOGLE not in cleaned
    assert set(kinds) == {"github", "aws", "google"}


def test_private_key_block_is_removed_entirely() -> None:
    cleaned, kinds = redact_text(f"config:\n{PRIVATE_KEY}\nend")
    assert "PRIVATE KEY" not in cleaned
    assert kinds == ["private_key"]


def test_email_and_card_are_redacted_by_redact_text() -> None:
    cleaned, kinds = redact_text("owner@cornershop.example paid with 4242 4242 4242 4242")
    assert "owner@cornershop.example" not in cleaned
    assert "4242 4242 4242 4242" not in cleaned
    assert set(kinds) == {"card", "email"}


def test_redact_secrets_preserves_operational_email_addresses() -> None:
    cleaned, kinds = redact_secrets(f"sender billing@paypa1-secure.example used {STRIPE}")
    assert "billing@paypa1-secure.example" in cleaned
    assert STRIPE not in cleaned
    assert kinds == ["stripe"]


def test_secret_redaction_is_recursive() -> None:
    payload = {"outer": [{"token": GITHUB}, "safe"], "nested": {"key": AWS}}
    cleaned, kinds = redact_secrets_obj(payload)
    assert GITHUB not in str(cleaned)
    assert AWS not in str(cleaned)
    assert set(kinds) == {"aws", "github"}


def test_full_redaction_walks_dicts_and_preserves_types() -> None:
    cleaned, kinds = redact_obj({"count": 3, "ok": True, "contact": "a@b.example"})
    assert cleaned["count"] == 3
    assert cleaned["ok"] is True
    assert cleaned["contact"] == "[REDACTED:email]"
    assert kinds == ["email"]


def test_pii_redaction_handles_phone_numbers() -> None:
    cleaned, kinds = redact_pii("call +1 555 123 4567 or mail ops@cornershop.example")
    assert "555" not in cleaned
    assert "ops@cornershop.example" not in cleaned
    assert set(kinds) == {"email", "phone"}


def test_mask_secret_keeps_identifying_endpoints() -> None:
    assert mask_secret(STRIPE) == "sk_l…i9J0"
    assert mask_secret("short") == "[REDACTED]"
