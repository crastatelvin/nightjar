"""Request correlation, body caps, and the response headers that carry them.

Every response gets an ``x-request-id``: the caller's if it sent a usable one, a
fresh one otherwise. That id is also bound to a context variable so log lines
emitted while the request is running can be joined to the response the caller
saw. A support ticket quoting the header is enough to find the whole trace.

The body cap is applied to every request, not just the signed intake. The intake
has its own, smaller cap because it is the only unauthenticated write path, but a
1 MiB JSON document posted to ``/v1/incidents`` is not a plausible request and
should be refused before it is parsed.
"""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from contextvars import ContextVar
from typing import Any

from fastapi import HTTPException, status
from starlette.datastructures import Headers, MutableHeaders

REQUEST_ID_HEADER = "x-request-id"
# Anything a proxy or client could reasonably generate: no whitespace, no
# newlines, no control characters, bounded length. A header that fails this is
# replaced rather than rejected, so a bad client library cannot break a request.
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

request_id_var: ContextVar[str] = ContextVar("nightjar_request_id", default="")


def current_request_id() -> str:
    return request_id_var.get()


class RequestIdFilter(logging.Filter):
    """Attach the active request id to every record.

    Filters on a logger do not run for records propagated from children, so this
    is installed on the handler instead. Records emitted outside a request show
    ``-`` rather than a stale id from a previous request.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


class RequestIdFormatter(logging.Formatter):
    """A formatter that accepts uvicorn's ``use_colors`` keyword.

    ``uvicorn.Config.configure_logging`` writes ``use_colors`` into the formatter
    config before applying ``dictConfig``. ``logging.Formatter`` has no such
    argument, so it is dropped here instead of failing at startup.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.pop("use_colors", None)
        super().__init__(*args, **kwargs)


def build_log_config(level: str) -> dict[str, Any]:
    """A ``dictConfig`` that prints the request id on every line.

    Uvicorn's own access logger is routed through the same handler, so an access
    line and the application lines it caused share one id.
    """
    line = "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request_id": {"()": "nightjar.api.middleware.RequestIdFilter"}},
        "formatters": {
            "default": {"()": "nightjar.api.middleware.RequestIdFormatter", "format": line},
            # Uvicorn writes into this key unconditionally when colours are on.
            "access": {"()": "nightjar.api.middleware.RequestIdFormatter", "format": line},
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "filters": ["request_id"],
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn.error": {"level": level},
            "uvicorn.access": {"handlers": ["default"], "level": level, "propagate": False},
            "nightjar": {"handlers": ["default"], "level": level, "propagate": False},
        },
        "root": {"handlers": ["default"], "level": level},
    }


class RequestContextMiddleware:
    """Assign a request id, bind it to logs, and echo it on the response.

    Also publishes the rate-limit snapshot the auth dependency recorded, so a
    caller can see its budget without a separate endpoint. Headers are written at
    ``http.response.start``, which means error responses carry them too.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = Headers(scope=scope).get(REQUEST_ID_HEADER, "").strip()
        request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else uuid.uuid4().hex
        state: dict[str, Any] = scope.setdefault("state", {})
        state["request_id"] = request_id
        token = request_id_var.set(request_id)

        async def send_with_context(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                decision = state.get("rate_limit")
                if decision is not None:
                    headers["x-ratelimit-limit"] = str(decision.limit)
                    headers["x-ratelimit-remaining"] = str(decision.remaining)
                    headers["x-ratelimit-reset"] = str(max(1, math.ceil(decision.reset_after)))
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        finally:
            request_id_var.reset(token)


class BodySizeLimitMiddleware:
    """Refuse a request body larger than ``max_bytes`` with ``413``.

    A declared ``Content-Length`` is checked before the body is touched. A chunked
    request is counted as it streams and abandoned at the limit, so an oversized
    upload costs one buffer's worth of memory instead of the whole document. The
    refusal is raised as an ``HTTPException`` from inside ``receive`` because that
    is what Starlette's exception middleware turns into a response at any point
    in the read.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = self._declared_length(scope)
        if declared is not None and declared > self.max_bytes:
            await self._too_large(send)
            return
        if scope["method"] not in BODY_METHODS:
            await self.app(scope, receive, send)
            return

        received = 0

        async def counted_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"request body exceeds {self.max_bytes} bytes",
                    )
            return message

        await self.app(scope, counted_receive, send)

    def _declared_length(self, scope: Scope) -> int | None:
        raw = Headers(scope=scope).get("content-length")
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    async def _too_large(self, send: Send) -> None:
        body = json.dumps({"detail": f"request body exceeds {self.max_bytes} bytes"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status.HTTP_413_CONTENT_TOO_LARGE,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


__all__ = [
    "REQUEST_ID_HEADER",
    "BodySizeLimitMiddleware",
    "RequestContextMiddleware",
    "RequestIdFilter",
    "RequestIdFormatter",
    "build_log_config",
    "current_request_id",
    "request_id_var",
]
