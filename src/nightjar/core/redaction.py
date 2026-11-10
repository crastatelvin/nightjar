from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"
            r".*?-----END (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----",
            re.S,
        ),
    ),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("stripe", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{12,}\b")),
    ("github", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("aws", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*")),
]

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_CARD = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+\d{1,3}[ .-]?)?(?:\(?\d{2,4}\)?[ .-]?)\d{3,4}[ .-]?\d{3,4}(?!\d)")


def mask_secret(value: str, keep: int = 4) -> str:
    """Keep a short prefix/suffix so a human can identify the credential."""
    if len(value) <= keep * 2:
        return "[REDACTED]"
    return f"{value[:keep]}…{value[-keep:]}"


def redact_text(text: str) -> tuple[str, list[str]]:
    """Redact common credential and PII patterns. Returns (text, kinds)."""
    found: list[str] = []
    result = text
    for kind, pattern in _SECRET_PATTERNS:

        def _replace(match: re.Match[str], kind: str = kind) -> str:
            found.append(kind)
            return f"[REDACTED:{kind}]"

        result = pattern.sub(_replace, result)
    if _EMAIL.search(result):
        found.append("email")
        result = _EMAIL.sub("[REDACTED:email]", result)
    if _CARD.search(result):
        found.append("card")
        result = _CARD.sub("[REDACTED:card]", result)
    return result, sorted(set(found))


def redact_obj(value: Any) -> tuple[Any, list[str]]:
    """Recursively redact strings in a JSON-compatible structure."""
    kinds: list[str] = []

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            cleaned, found = redact_text(item)
            kinds.extend(found)
            return cleaned
        if isinstance(item, dict):
            return {key: walk(val) for key, val in item.items()}
        if isinstance(item, list):
            return [walk(val) for val in item]
        return item

    return walk(value), sorted(set(kinds))


def redact_secrets(text: str) -> tuple[str, list[str]]:
    """Remove credentials while preserving operational data such as sender addresses."""
    found: list[str] = []
    result = text
    for kind, pattern in _SECRET_PATTERNS:

        def _replace(match: re.Match[str], kind: str = kind) -> str:
            found.append(kind)
            return f"[REDACTED:{kind}]"

        result = pattern.sub(_replace, result)
    if _CARD.search(result):
        found.append("card")
        result = _CARD.sub("[REDACTED:card]", result)
    return result, sorted(set(found))


def redact_secrets_obj(value: Any) -> tuple[Any, list[str]]:
    kinds: list[str] = []

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            cleaned, found = redact_secrets(item)
            kinds.extend(found)
            return cleaned
        if isinstance(item, dict):
            return {key: walk(val) for key, val in item.items()}
        if isinstance(item, list):
            return [walk(val) for val in item]
        return item

    return walk(value), sorted(set(kinds))


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Redact contact data without touching logs needed for diagnosis."""
    found: list[str] = []
    if _EMAIL.search(text):
        found.append("email")
        text = _EMAIL.sub("[REDACTED:email]", text)
    if _PHONE.search(text):
        found.append("phone")
        text = _PHONE.sub("[REDACTED:phone]", text)
    return text, found
