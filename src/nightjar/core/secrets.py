from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol


class SecretError(RuntimeError):
    """A secret reference could not be resolved. Never includes the value."""


class SecretVault(Protocol):
    def resolve(self, ref: str) -> str: ...


class EnvVault:
    """Resolve ``env:NAME`` and ``file:/path`` references.

    The raw value is read at the moment it is needed and never stored. Action
    parameters carry the reference, so the database, the timeline, and the model
    prompt never contain the credential itself.
    """

    def resolve(self, ref: str) -> str:
        scheme, _, target = ref.partition(":")
        if scheme == "env":
            if not target:
                raise SecretError("empty environment secret reference")
            value = os.environ.get(target)
            if value is None or not value.strip():
                raise SecretError(f"environment secret {target!r} is not set")
            return value.strip()
        if scheme == "file":
            if not target:
                raise SecretError("empty file secret reference")
            path = Path(target).expanduser()
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise SecretError(f"cannot read secret file {target!r}: {exc.strerror}") from exc
            if not value:
                raise SecretError(f"secret file {target!r} is empty")
            return value
        raise SecretError("secret references must start with env: or file:")


def fingerprint(value: str) -> str:
    """Stable, non-reversible identifier used in evidence and logs."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def hint(value: str) -> str:
    """Last four characters, for a human to recognise which key this is."""
    return value[-4:] if len(value) >= 4 else "****"


GITHUB_CREDENTIAL_PREFIXES = ("ghp_", "github_pat_", "gho_", "ghu_", "ghr_")


def is_github_credential(value: str) -> bool:
    return value.startswith(GITHUB_CREDENTIAL_PREFIXES)
