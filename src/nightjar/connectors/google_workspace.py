from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import jwt

from nightjar.connectors.base import Capability, CapabilityReport, ConnectorError, ToolResult
from nightjar.core.enums import ActionType, EvidenceKind
from nightjar.core.models import ActionSpec, VerificationPredicate
from nightjar.core.verification import VerificationError

GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SETTINGS = "https://www.googleapis.com/auth/gmail.settings.basic"
DEFAULT_SCOPES = (GMAIL_MODIFY, GMAIL_SETTINGS)
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
QUARANTINE_LABEL = "Nightjar-Quarantine"


class GoogleWorkspaceConnector:
    """Gmail quarantine and sender blocking through a delegated service account.

    Domain-wide delegation is the only supported mode: the service account
    impersonates the mailbox it is acting on, so Nightjar never holds a user's
    password or refresh token. Both actions are read back by the verifier —
    a `200` from `messages.modify` is not proof that the message left the inbox.
    """

    name = "google"
    action_types = frozenset({ActionType.QUARANTINE_MESSAGE, ActionType.BLOCK_SENDER})
    tool_names = frozenset({"google.message"})

    def __init__(
        self,
        service_account_file: str,
        *,
        mailboxes: tuple[str, ...] = (),
        api_url: str = "https://gmail.googleapis.com",
        oauth_url: str = "https://oauth2.googleapis.com/token",
        quarantine_label: str = QUARANTINE_LABEL,
        scopes: tuple[str, ...] = DEFAULT_SCOPES,
        timeout: float = 20.0,
    ) -> None:
        self._key_file = Path(service_account_file).expanduser()
        self._mailboxes = tuple(mailbox.lower() for mailbox in mailboxes)
        self._api = api_url.rstrip("/")
        self._oauth_url = oauth_url
        self._label = quarantine_label
        self._scopes = scopes
        self._timeout = timeout
        self._account: dict[str, str] | None = None
        self._tokens: dict[tuple[str, tuple[str, ...]], tuple[str, float]] = {}

    # ------------------------------------------------------------------ auth
    def _load_account(self) -> dict[str, str]:
        if self._account is not None:
            return self._account
        try:
            raw = json.loads(self._key_file.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConnectorError(
                f"cannot read Google service account file {self._key_file}: {exc.strerror}"
            ) from exc
        except ValueError as exc:
            raise ConnectorError("Google service account file is not valid JSON") from exc
        missing = [
            key for key in ("client_email", "private_key") if not str(raw.get(key, "")).strip()
        ]
        if missing:
            raise ConnectorError(
                "Google service account file is missing " + ", ".join(sorted(missing))
            )
        account = {
            "client_email": str(raw["client_email"]),
            "private_key": str(raw["private_key"]),
            "private_key_id": str(raw.get("private_key_id", "")),
            "token_uri": str(raw.get("token_uri") or self._oauth_url),
        }
        self._account = account
        return account

    async def _access_token(self, mailbox: str, scopes: tuple[str, ...]) -> str:
        key = (mailbox, scopes)
        cached = self._tokens.get(key)
        now = time.time()
        if cached is not None and cached[1] > now + 30:
            return cached[0]
        account = self._load_account()
        issued = int(now)
        claims = {
            "iss": account["client_email"],
            "sub": mailbox,
            "scope": " ".join(scopes),
            "aud": account["token_uri"],
            "iat": issued,
            "exp": issued + 3600,
        }
        assertion = jwt.encode(
            claims,
            account["private_key"],
            algorithm="RS256",
            headers={"kid": account["private_key_id"]},
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    account["token_uri"],
                    data={"grant_type": JWT_BEARER, "assertion": assertion},
                )
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"Google token endpoint unreachable: {exc}", retryable=True
            ) from exc
        if response.status_code >= 400:
            detail = _google_error(response)
            raise ConnectorError(
                "Google refused the delegated token for "
                f"{mailbox}: {detail}. Check domain-wide delegation and the "
                "gmail.modify / gmail.settings.basic scopes."
            )
        body = response.json()
        token = str(body.get("access_token", ""))
        if not token:
            raise ConnectorError("Google token response contained no access_token")
        expires_in = int(body.get("expires_in", 3600))
        self._tokens[key] = (token, now + expires_in)
        return token

    # --------------------------------------------------------------- requests
    async def _request(
        self,
        method: str,
        path: str,
        mailbox: str,
        *,
        scopes: tuple[str, ...] = (GMAIL_MODIFY,),
        **kwargs: Any,
    ) -> httpx.Response:
        token = await self._access_token(mailbox, scopes)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.request(
                    method,
                    f"{self._api}{path}",
                    headers={"authorization": f"Bearer {token}"},
                    **kwargs,
                )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Gmail unreachable: {exc}", retryable=True) from exc

    async def _json(
        self,
        method: str,
        path: str,
        mailbox: str,
        *,
        scopes: tuple[str, ...] = (GMAIL_MODIFY,),
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = await self._request(method, path, mailbox, scopes=scopes, **kwargs)
        if response.status_code == 404:
            raise ConnectorError(f"Gmail object not found: {path}")
        if response.status_code == 403:
            raise ConnectorError(
                "Gmail refused the request (403): the delegated account lacks the "
                "required scope or the mailbox is not covered by delegation"
            )
        if response.status_code >= 400:
            raise ConnectorError(
                f"Gmail returned {response.status_code}: {_google_error(response)}"
            )
        body: dict[str, Any] = response.json() if response.content else {}
        return body

    # ----------------------------------------------------------------- target
    def _mailbox(self, explicit: Any) -> str:
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip().lower()
        if len(self._mailboxes) == 1:
            return self._mailboxes[0]
        if not self._mailboxes:
            raise ConnectorError(
                "google action needs a mailbox: set params.mailbox or list mailboxes "
                "in NIGHTJAR_MANAGED_TARGETS"
            )
        raise ConnectorError("google action needs params.mailbox: more than one mailbox is managed")

    async def _label_id(self, mailbox: str, name: str) -> str:
        body = await self._json("GET", f"/gmail/v1/users/{_q(mailbox)}/labels", mailbox)
        for label in body.get("labels", []):
            if str(label.get("name", "")).lower() == name.lower():
                return str(label["id"])
        created = await self._json(
            "POST",
            f"/gmail/v1/users/{_q(mailbox)}/labels",
            mailbox,
            json={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        return str(created["id"])

    async def _filters(self, mailbox: str) -> list[dict[str, Any]]:
        body = await self._json(
            "GET",
            f"/gmail/v1/users/{_q(mailbox)}/settings/filters",
            mailbox,
            scopes=(GMAIL_SETTINGS,),
        )
        filters: list[dict[str, Any]] = body.get("filter", [])
        return filters

    # ---------------------------------------------------------------- actions
    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        if action.action_type is ActionType.QUARANTINE_MESSAGE:
            return await self._quarantine(action)
        if action.action_type is ActionType.BLOCK_SENDER:
            return await self._block_sender(action)
        raise ConnectorError(f"google connector does not implement {action.action_type}")

    async def _quarantine(self, action: ActionSpec) -> dict[str, Any]:
        message_id = str(action.params.get("message_id", "")).strip()
        if not message_id:
            raise ConnectorError("quarantine_message requires message_id")
        mailbox = self._mailbox(action.params.get("mailbox"))
        label = str(action.params.get("quarantine_label") or self._label)
        label_id = await self._label_id(mailbox, label)
        await self._json(
            "POST",
            f"/gmail/v1/users/{_q(mailbox)}/messages/{_q(message_id)}/modify",
            mailbox,
            json={"addLabelIds": [label_id], "removeLabelIds": ["INBOX", "UNREAD"]},
        )
        return {
            "mailbox": mailbox,
            "message_id": message_id,
            "label": label,
            "label_id": label_id,
            "removed": ["INBOX", "UNREAD"],
        }

    async def _block_sender(self, action: ActionSpec) -> dict[str, Any]:
        sender = str(action.params.get("sender", "")).strip()
        if not sender:
            raise ConnectorError("block_sender requires sender")
        mailbox = self._mailbox(action.params.get("mailbox"))
        label = str(action.params.get("quarantine_label") or self._label)
        label_id = await self._label_id(mailbox, label)

        for existing in await self._filters(mailbox):
            criteria = existing.get("criteria") or {}
            target_action = existing.get("action") or {}
            if str(criteria.get("from", "")).lower() == sender.lower() and label_id in (
                target_action.get("addLabelIds") or []
            ):
                return {
                    "mailbox": mailbox,
                    "sender": sender,
                    "label": label,
                    "filter_id": str(existing.get("id", "")),
                    "replay": True,
                }

        created = await self._json(
            "POST",
            f"/gmail/v1/users/{_q(mailbox)}/settings/filters",
            mailbox,
            scopes=(GMAIL_SETTINGS,),
            json={
                "criteria": {"from": sender},
                "action": {"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]},
            },
        )
        return {
            "mailbox": mailbox,
            "sender": sender,
            "label": label,
            "filter_id": str(created.get("id", "")),
            "replay": False,
        }

    # ------------------------------------------------------------- read tools
    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult:
        if tool != "google.message":
            raise ConnectorError(f"unknown google tool {tool}")
        mailbox = self._mailbox(params.get("mailbox"))
        message_id = str(params.get("message_id", "")).strip()
        if not message_id:
            raise ConnectorError("google.message requires message_id")
        body = await self._json(
            "GET",
            f"/gmail/v1/users/{_q(mailbox)}/messages/{_q(message_id)}",
            mailbox,
            params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]},
        )
        headers = {
            str(item.get("name", "")).lower(): str(item.get("value", ""))
            for item in (body.get("payload") or {}).get("headers", [])
        }
        return ToolResult(
            kind=EvidenceKind.EMAIL,
            source=f"google:{mailbox}/{message_id}",
            summary=f"Read message {message_id} from {mailbox}",
            content={
                "mailbox": mailbox,
                "message_id": message_id,
                "thread_id": body.get("threadId"),
                "label_ids": body.get("labelIds", []),
                "from": headers.get("from", ""),
                "to": headers.get("to", ""),
                "subject": headers.get("subject", ""),
                "date": headers.get("date", ""),
                "snippet": str(body.get("snippet", ""))[:1000],
            },
        )

    # ------------------------------------------------------------ verification
    def verification_checks(self) -> dict[str, Any]:
        return {
            "google.message_quarantined": self._check_message_quarantined,
            "google.filter_present": self._check_filter_present,
        }

    async def _check_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """A connector failure is a failed predicate, not a crashed verifier."""
        try:
            return await self._json(*args, **kwargs)
        except ConnectorError as exc:
            raise VerificationError(str(exc)) from exc

    async def _check_label_id(self, mailbox: str, name: str) -> str:
        try:
            return await self._label_id(mailbox, name)
        except ConnectorError as exc:
            raise VerificationError(str(exc)) from exc

    async def _check_filters(self, mailbox: str) -> list[dict[str, Any]]:
        try:
            return await self._filters(mailbox)
        except ConnectorError as exc:
            raise VerificationError(str(exc)) from exc

    async def _check_message_quarantined(self, predicate: VerificationPredicate) -> dict[str, Any]:
        mailbox = self._mailbox(predicate.params.get("mailbox"))
        message_id = str(predicate.params.get("message_id", "")).strip()
        if not message_id:
            raise VerificationError("google.message_quarantined requires message_id")
        label = str(predicate.params.get("quarantine_label") or self._label)
        label_id = await self._check_label_id(mailbox, label)
        body = await self._check_json(
            "GET",
            f"/gmail/v1/users/{_q(mailbox)}/messages/{_q(message_id)}",
            mailbox,
            params={"format": "minimal"},
        )
        labels = [str(item) for item in body.get("labelIds", [])]
        return {
            "mailbox": mailbox,
            "message_id": message_id,
            "label": label,
            "label_ids": labels,
            "passed": label_id in labels and "INBOX" not in labels,
        }

    async def _check_filter_present(self, predicate: VerificationPredicate) -> dict[str, Any]:
        mailbox = self._mailbox(predicate.params.get("mailbox"))
        sender = str(predicate.params.get("sender", "")).strip()
        if not sender:
            raise VerificationError("google.filter_present requires sender")
        label = str(predicate.params.get("quarantine_label") or self._label)
        label_id = await self._check_label_id(mailbox, label)
        for existing in await self._check_filters(mailbox):
            criteria = existing.get("criteria") or {}
            target_action = existing.get("action") or {}
            if str(criteria.get("from", "")).lower() == sender.lower() and label_id in (
                target_action.get("addLabelIds") or []
            ):
                return {
                    "mailbox": mailbox,
                    "sender": sender,
                    "filter_id": str(existing.get("id", "")),
                    "passed": True,
                }
        return {"mailbox": mailbox, "sender": sender, "filter_id": None, "passed": False}

    # ------------------------------------------------------------- capability
    def capabilities(self) -> tuple[Capability, ...]:
        return (
            Capability(
                action_type=ActionType.QUARANTINE_MESSAGE,
                provider=self.name,
                target_kinds=("mailbox", "message"),
                notes="Gmail label plus INBOX removal; one message per action",
            ),
            Capability(
                action_type=ActionType.BLOCK_SENDER,
                provider=self.name,
                target_kinds=("mailbox", "sender"),
                notes="Gmail filter; mailbox comes from the action or the single managed mailbox",
            ),
        )

    async def preflight(self, *, deep: bool = False) -> CapabilityReport:
        if not self._mailboxes:
            return CapabilityReport(
                provider=self.name,
                verdict="degraded",
                identity="service account",
                detail="no mailboxes configured in NIGHTJAR_MANAGED_TARGETS",
            )
        mailbox = self._mailboxes[0]
        started = time.monotonic()
        try:
            body = await self._json("GET", f"/gmail/v1/users/{_q(mailbox)}/profile", mailbox)
        except ConnectorError as exc:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                identity=f"delegated:{mailbox}",
                detail=str(exc),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        return CapabilityReport(
            provider=self.name,
            verdict="ok",
            identity=f"delegated:{body.get('emailAddress', mailbox)}",
            detail=f"{len(self._mailboxes)} managed mailbox(es)",
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def _q(value: str) -> str:
    return quote(value, safe="")


def _google_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or f"HTTP {response.status_code}"
    error = body.get("error")
    if isinstance(error, dict):
        message = str(error.get("message", ""))[:200]
        status = str(error.get("status", ""))
        return f"{status}: {message}".strip(": ") or f"HTTP {response.status_code}"
    if error:
        return str(error)[:200]
    return response.text[:200] or f"HTTP {response.status_code}"
