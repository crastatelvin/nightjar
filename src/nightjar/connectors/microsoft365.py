"""Microsoft 365 mail containment and session revocation.

Auth is an Entra application with admin-consented *application* permissions, so
Nightjar acts on a mailbox without holding a user's password. Quarantine is a
folder move, which Graph implements by creating a new message id; the action
result carries it so the plan can verify the copy that actually exists.

Session revocation is not instant and is not account disable. The verification
check reads `signInActivity` and requires no successful sign-in after the
request, with a grace window for Graph's propagation delay. A tenant without
`AuditLog.Read.All` and Entra ID P1 cannot prove the revocation, and Nightjar
says so instead of accepting the API's `true`.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from nightjar.connectors.base import (
    Capability,
    CapabilityReport,
    CheckFn,
    ConnectorError,
    ToolResult,
)
from nightjar.connectors.errors import (
    AuthError,
    NotFound,
    PermissionDenied,
    RateLimited,
    error_from_response,
    retry_after_seconds,
    transport_error,
)
from nightjar.connectors.ratelimit import limiter_for
from nightjar.core.enums import ActionType, EvidenceKind
from nightjar.core.ids import utcnow
from nightjar.core.models import ActionSpec, VerificationPredicate
from nightjar.core.verification import VerificationError

GRAPH_URL = "https://graph.microsoft.com/v1.0"
LOGIN_URL = "https://login.microsoftonline.com"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
QUARANTINE_FOLDER = "Nightjar Quarantine"
DEFAULT_PROPAGATION_SECONDS = 180


class Microsoft365Connector:
    """Move messages, add inbox rules, and revoke sessions through Graph."""

    name = "microsoft"
    action_types = frozenset(
        {
            ActionType.QUARANTINE_MESSAGE,
            ActionType.BLOCK_SENDER,
            ActionType.REVOKE_CREDENTIAL,
        }
    )
    tool_names = frozenset({"microsoft.message"})

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        *,
        mailboxes: tuple[str, ...] = (),
        graph_url: str = GRAPH_URL,
        login_url: str = LOGIN_URL,
        quarantine_folder: str = QUARANTINE_FOLDER,
        propagation_seconds: int = DEFAULT_PROPAGATION_SECONDS,
        timeout: float = 20.0,
    ) -> None:
        self._tenant = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._mailboxes = tuple(mailbox.lower() for mailbox in mailboxes)
        self._graph = graph_url.rstrip("/")
        self._login = login_url.rstrip("/")
        self._folder = quarantine_folder
        self._propagation = max(propagation_seconds, 0)
        self._timeout = timeout
        self._token: tuple[str, float] | None = None

    # ------------------------------------------------------------------ auth
    async def _access_token(self) -> str:
        now = time.time()
        if self._token is not None and self._token[1] > now + 60:
            return self._token[0]
        url = f"{self._login}/{_q(self._tenant)}/oauth2/v2.0/token"
        await limiter_for(self.name).acquire()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "scope": GRAPH_SCOPE,
                    },
                )
        except httpx.HTTPError as exc:
            raise transport_error(self.name, exc, "client credentials grant") from exc
        if response.status_code >= 400:
            raise ConnectorError(
                "Microsoft refused the client credentials grant: "
                f"{_graph_error(response)}. Check NIGHTJAR_MICROSOFT_TENANT_ID, "
                "NIGHTJAR_MICROSOFT_CLIENT_ID, NIGHTJAR_MICROSOFT_CLIENT_SECRET, and "
                "admin consent.",
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        body = response.json()
        token = str(body.get("access_token", ""))
        if not token:
            raise ConnectorError("Microsoft token response contained no access_token")
        self._token = (token, now + int(body.get("expires_in", 3600)))
        return token

    # --------------------------------------------------------------- requests
    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any
    ) -> httpx.Response:
        token = await self._access_token()
        await limiter_for(self.name).acquire()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.request(
                    method,
                    f"{self._graph}{path}",
                    headers={"authorization": f"Bearer {token}"},
                    params=params,
                    **kwargs,
                )
        except httpx.HTTPError as exc:
            raise transport_error(self.name, exc, f"{method} {path}") from exc

    async def _json(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        response = await self._request(method, path, params=params, **kwargs)
        if response.status_code == 429:
            wait = retry_after_seconds(response)
            raise RateLimited(
                f"Microsoft Graph rate limited; retry after {wait if wait is not None else 1:g}s",
                retry_after=wait,
                status=429,
                provider=self.name,
            )
        if response.status_code == 401:
            raise AuthError(
                "Microsoft Graph rejected the access token (401); the app credentials "
                "or tenant are wrong",
                status=401,
                provider=self.name,
            )
        if response.status_code == 403:
            raise PermissionDenied(
                "Microsoft Graph refused the request (403): the app lacks admin-consented "
                "application permission for this mailbox, or an application access policy "
                f"excludes it. {_graph_error(response)}",
                status=403,
                provider=self.name,
            )
        if response.status_code == 404:
            raise NotFound(
                f"Microsoft Graph object not found: {path}", status=404, provider=self.name
            )
        if response.status_code >= 400:
            raise error_from_response(self.name, response, f"Graph {method} {path}")
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
                "microsoft action needs a mailbox: set params.mailbox or list mailboxes "
                "in NIGHTJAR_MANAGED_TARGETS"
            )
        raise ConnectorError(
            "microsoft action needs params.mailbox: more than one mailbox is managed"
        )

    def _principal(self, action: ActionSpec) -> str:
        for key in ("mailbox", "upn", "user", "user_id", "credential_id"):
            value = action.params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        return self._mailbox(None)

    async def _find_folder(self, mailbox: str, name: str) -> str | None:
        body = await self._json("GET", f"/users/{_q(mailbox)}/mailFolders", params={"$top": "200"})
        for folder in body.get("value", []):
            if str(folder.get("displayName", "")).lower() == name.lower():
                return str(folder["id"])
        return None

    async def _folder_id(self, mailbox: str, name: str) -> str:
        existing = await self._find_folder(mailbox, name)
        if existing is not None:
            return existing
        created = await self._json(
            "POST", f"/users/{_q(mailbox)}/mailFolders", json={"displayName": name}
        )
        return str(created["id"])

    async def _rules(self, mailbox: str) -> list[dict[str, Any]]:
        body = await self._json("GET", f"/users/{_q(mailbox)}/mailFolders/inbox/messageRules")
        rules: list[dict[str, Any]] = body.get("value", [])
        return rules

    # ---------------------------------------------------------------- actions
    async def execute(self, action: ActionSpec) -> dict[str, Any]:
        if action.action_type is ActionType.QUARANTINE_MESSAGE:
            return await self._quarantine(action)
        if action.action_type is ActionType.BLOCK_SENDER:
            return await self._block_sender(action)
        if action.action_type is ActionType.REVOKE_CREDENTIAL:
            return await self._revoke_sessions(action)
        raise ConnectorError(f"microsoft connector does not implement {action.action_type}")

    async def _quarantine(self, action: ActionSpec) -> dict[str, Any]:
        message_id = str(
            action.params.get("message_id") or action.params.get("new_message_id") or ""
        ).strip()
        if not message_id:
            raise ConnectorError("quarantine_message requires message_id")
        mailbox = self._mailbox(action.params.get("mailbox"))
        folder = str(action.params.get("quarantine_folder") or self._folder)
        folder_id = await self._folder_id(mailbox, folder)
        moved = await self._json(
            "POST",
            f"/users/{_q(mailbox)}/messages/{_q(message_id)}/move",
            json={"destinationId": folder_id},
        )
        new_id = str(moved.get("id", "")).strip()
        return {
            "mailbox": mailbox,
            "message_id": message_id,
            "new_message_id": new_id or message_id,
            "folder": folder,
            "folder_id": folder_id,
            "parent_folder_id": str(moved.get("parentFolderId", "")),
        }

    async def _block_sender(self, action: ActionSpec) -> dict[str, Any]:
        sender = str(action.params.get("sender", "")).strip()
        if not sender:
            raise ConnectorError("block_sender requires sender")
        mailbox = self._mailbox(action.params.get("mailbox"))
        folder = str(action.params.get("quarantine_folder") or self._folder)
        folder_id = await self._folder_id(mailbox, folder)
        display_name = f"Nightjar: {sender}"

        for rule in await self._rules(mailbox):
            if str(rule.get("displayName", "")) == display_name and _rule_targets(rule, sender):
                return {
                    "mailbox": mailbox,
                    "sender": sender,
                    "rule_id": str(rule.get("id", "")),
                    "display_name": display_name,
                    "folder_id": folder_id,
                    "replay": True,
                }

        created = await self._json(
            "POST",
            f"/users/{_q(mailbox)}/mailFolders/inbox/messageRules",
            json={
                "displayName": display_name,
                "sequence": 1,
                "isEnabled": True,
                "conditions": {
                    "fromAddresses": [{"emailAddress": {"address": sender}}],
                },
                "actions": {
                    "moveToFolder": folder_id,
                    "stopProcessingRules": True,
                },
            },
        )
        return {
            "mailbox": mailbox,
            "sender": sender,
            "rule_id": str(created.get("id", "")),
            "display_name": display_name,
            "folder_id": folder_id,
            "replay": False,
        }

    async def _revoke_sessions(self, action: ActionSpec) -> dict[str, Any]:
        user = self._principal(action)
        requested_at = utcnow().isoformat()
        body = await self._json("POST", f"/users/{_q(user)}/revokeSignInSessions")
        if body.get("value") is not True:
            raise ConnectorError(
                "Microsoft Graph did not confirm session revocation; the API returned "
                f"{body.get('value')!r}"
            )
        return {
            "user": user,
            "mailbox": user,
            "revoked": True,
            "requested_at": requested_at,
        }

    # ------------------------------------------------------------- read tools
    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult:
        if tool != "microsoft.message":
            raise ConnectorError(f"unknown microsoft tool {tool}")
        mailbox = self._mailbox(params.get("mailbox"))
        message_id = str(params.get("message_id", "")).strip()
        if not message_id:
            raise ConnectorError("microsoft.message requires message_id")
        body = await self._json(
            "GET",
            f"/users/{_q(mailbox)}/messages/{_q(message_id)}",
            params={
                "$select": (
                    "id,parentFolderId,internetMessageId,subject,receivedDateTime,"
                    "bodyPreview,from,toRecipients"
                )
            },
        )
        sender = (body.get("from") or {}).get("emailAddress") or {}
        recipients = [
            str((item.get("emailAddress") or {}).get("address", ""))
            for item in body.get("toRecipients", [])
        ]
        return ToolResult(
            kind=EvidenceKind.EMAIL,
            source=f"microsoft:{mailbox}/{message_id}",
            summary=f"Read message {message_id} from {mailbox}",
            content={
                "mailbox": mailbox,
                "message_id": str(body.get("id", message_id)),
                "parent_folder_id": str(body.get("parentFolderId", "")),
                "internet_message_id": str(body.get("internetMessageId", "")),
                "from": str(sender.get("address", "")),
                "to": recipients,
                "subject": str(body.get("subject", "")),
                "received_at": str(body.get("receivedDateTime", "")),
                "snippet": str(body.get("bodyPreview", ""))[:1000],
            },
        )

    # ------------------------------------------------------------ verification
    def verification_checks(self) -> dict[str, CheckFn]:
        return {
            "microsoft.message_folder": self._check_message_folder,
            "microsoft.rule_present": self._check_rule_present,
            "microsoft.sessions_revoked": self._check_sessions_revoked,
        }

    async def _check_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """A connector failure is a failed predicate, not a crashed verifier."""
        try:
            return await self._json(*args, **kwargs)
        except ConnectorError as exc:
            raise VerificationError(str(exc)) from exc

    async def _check_folder_id(self, mailbox: str, folder: str) -> str:
        """Verification never creates the quarantine folder: it must already exist."""
        try:
            found = await self._find_folder(mailbox, folder)
        except ConnectorError as exc:
            raise VerificationError(str(exc)) from exc
        if found is None:
            raise VerificationError(f"quarantine folder {folder!r} does not exist for {mailbox}")
        return found

    def _check_mailbox(self, explicit: Any) -> str:
        try:
            return self._mailbox(explicit)
        except ConnectorError as exc:
            raise VerificationError(str(exc)) from exc

    async def _check_rules(self, mailbox: str) -> list[dict[str, Any]]:
        try:
            return await self._rules(mailbox)
        except ConnectorError as exc:
            raise VerificationError(str(exc)) from exc

    async def _check_message_folder(self, predicate: VerificationPredicate) -> dict[str, Any]:
        mailbox = self._check_mailbox(predicate.params.get("mailbox"))
        candidates = [
            str(predicate.params.get(key, "")).strip()
            for key in ("message_id", "new_message_id", "previous_message_id")
        ]
        candidates = [candidate for candidate in candidates if candidate]
        if not candidates:
            raise VerificationError("microsoft.message_folder requires message_id")
        folder = str(predicate.params.get("folder") or self._folder)
        folder_id = await self._check_folder_id(mailbox, folder)
        for candidate in candidates:
            try:
                body = await self._check_json(
                    "GET",
                    f"/users/{_q(mailbox)}/messages/{_q(candidate)}",
                    params={"$select": "id,parentFolderId,subject"},
                )
            except VerificationError as exc:
                if "not found" in str(exc).lower():
                    continue
                raise
            parent = str(body.get("parentFolderId", ""))
            return {
                "mailbox": mailbox,
                "message_id": candidate,
                "folder": folder,
                "folder_id": folder_id,
                "parent_folder_id": parent,
                "passed": parent == folder_id,
            }
        return {
            "mailbox": mailbox,
            "message_id": candidates[0],
            "folder": folder,
            "folder_id": folder_id,
            "parent_folder_id": None,
            "passed": False,
            "reason": "message is not visible under any known id",
        }

    async def _check_rule_present(self, predicate: VerificationPredicate) -> dict[str, Any]:
        mailbox = self._check_mailbox(predicate.params.get("mailbox"))
        sender = str(predicate.params.get("sender", "")).strip()
        if not sender:
            raise VerificationError("microsoft.rule_present requires sender")
        display_name = str(predicate.params.get("display_name") or f"Nightjar: {sender}")
        for rule in await self._check_rules(mailbox):
            if str(rule.get("displayName", "")) == display_name and _rule_targets(rule, sender):
                return {
                    "mailbox": mailbox,
                    "sender": sender,
                    "rule_id": str(rule.get("id", "")),
                    "display_name": display_name,
                    "enabled": bool(rule.get("isEnabled", True)),
                    "passed": True,
                }
        return {
            "mailbox": mailbox,
            "sender": sender,
            "rule_id": None,
            "display_name": display_name,
            "enabled": False,
            "passed": False,
        }

    async def _check_sessions_revoked(self, predicate: VerificationPredicate) -> dict[str, Any]:
        user = self._check_mailbox(
            predicate.params.get("mailbox")
            or predicate.params.get("upn")
            or predicate.params.get("user")
        )
        since_raw = str(predicate.params.get("since", "")).strip()
        if not since_raw:
            raise VerificationError(
                "microsoft.sessions_revoked requires since (the revocation timestamp)"
            )
        since = _parse_time(since_raw, "since")
        grace = int(predicate.params.get("grace_seconds", self._propagation))
        body = await self._check_json(
            "GET", f"/users/{_q(user)}", params={"$select": "id,userPrincipalName,signInActivity"}
        )
        activity = body.get("signInActivity")
        if not isinstance(activity, dict):
            raise VerificationError(
                f"Microsoft Graph returned no signInActivity for {user}: Nightjar cannot "
                "prove the revocation. The app needs AuditLog.Read.All and the tenant "
                "needs Entra ID P1, and an account with no sign-in history cannot be "
                "proven either. The API's true is not accepted as proof."
            )
        interactive = _optional_time(activity.get("lastSignInDateTime"))
        non_interactive = _optional_time(activity.get("lastNonInteractiveSignInDateTime"))
        latest = max(
            (moment for moment in (interactive, non_interactive) if moment is not None),
            default=None,
        )
        deadline = since + timedelta(seconds=max(grace, 0))
        return {
            "user": user,
            "since": since.isoformat(),
            "grace_seconds": grace,
            "last_sign_in": interactive.isoformat() if interactive else None,
            "last_non_interactive_sign_in": (
                non_interactive.isoformat() if non_interactive else None
            ),
            "passed": latest is None or latest <= deadline,
        }

    # ------------------------------------------------------------- capability
    def capabilities(self) -> tuple[Capability, ...]:
        return (
            Capability(
                action_type=ActionType.QUARANTINE_MESSAGE,
                provider=self.name,
                target_kinds=("mailbox", "message"),
                notes=(
                    "Graph move into the Nightjar Quarantine folder; the move creates a "
                    "new message id, which the action returns"
                ),
            ),
            Capability(
                action_type=ActionType.BLOCK_SENDER,
                provider=self.name,
                target_kinds=("mailbox", "sender"),
                notes="inbox message rule; read back from messageRules before resolution",
            ),
            Capability(
                action_type=ActionType.REVOKE_CREDENTIAL,
                provider=self.name,
                target_kinds=("mailbox", "user"),
                notes=(
                    "revokeSignInSessions; proof reads signInActivity and needs "
                    "AuditLog.Read.All and Entra ID P1. Not account disable."
                ),
            ),
        )

    async def preflight(self, *, deep: bool = False) -> CapabilityReport:
        if not self._mailboxes:
            return CapabilityReport(
                provider=self.name,
                verdict="degraded",
                identity="app registration",
                detail="no mailboxes configured in NIGHTJAR_MANAGED_TARGETS",
            )
        mailbox = self._mailboxes[0]
        started = time.monotonic()
        try:
            body = await self._json(
                "GET", f"/users/{_q(mailbox)}", params={"$select": "id,userPrincipalName"}
            )
        except ConnectorError as exc:
            return CapabilityReport(
                provider=self.name,
                verdict="error",
                identity=f"app:{mailbox}",
                detail=str(exc),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        return CapabilityReport(
            provider=self.name,
            verdict="ok",
            identity=f"user:{body.get('userPrincipalName', mailbox)}",
            detail=f"{len(self._mailboxes)} managed mailbox(es)",
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def _q(value: str) -> str:
    return quote(value, safe="")


def _graph_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code", "")).strip()
        message = str(error.get("message", "")).strip()
        joined = ": ".join(part for part in (code, message) if part)
        return joined[:300] or f"HTTP {response.status_code}"
    return str(body)[:300]


def _rule_targets(rule: dict[str, Any], sender: str) -> bool:
    conditions = rule.get("conditions") or {}
    for entry in conditions.get("fromAddresses") or []:
        if isinstance(entry, str):
            address = entry
        else:
            address = str(((entry or {}).get("emailAddress") or {}).get("address", ""))
        if address.strip().lower() == sender.lower():
            return True
    return False


def _parse_time(value: str, label: str) -> datetime:
    parsed = _optional_time(value)
    if parsed is None:
        raise VerificationError(f"microsoft.sessions_revoked {label} is not a timestamp")
    return parsed


def _optional_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


__all__ = [
    "DEFAULT_PROPAGATION_SECONDS",
    "GRAPH_URL",
    "QUARANTINE_FOLDER",
    "Microsoft365Connector",
]
