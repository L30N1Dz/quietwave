"""Everything that talks to Twilio.

Two jobs:

1. Validate inbound webhook signatures. The URL handed to the validator is
   built from PUBLIC_BASE_URL plus the known route path -- never from the
   inbound request, because behind Cloudflare Tunnel the request's own idea
   of its URL (scheme, host, port) is not what Twilio signed.
2. Send outbound messages. The Twilio SDK is synchronous, so every call goes
   through a threadpool to keep the event loop free.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from starlette.concurrency import run_in_threadpool
from twilio.base.exceptions import TwilioRestException
from twilio.request_validator import RequestValidator
from twilio.rest import Client

from app.config import Settings

log = logging.getLogger("quietwave.twilio")

# Twilio error codes worth translating. A teenager staring at "Error 21610"
# learns nothing; these are the failures that actually happen in practice.
_FRIENDLY_ERRORS = {
    21610: "That number has replied STOP to this line, so Twilio is blocking "
           "messages to it. They have to text START to unblock it.",
    21408: "This Twilio account is not permitted to message that region yet.",
    21211: "That number is not a valid phone number.",
    21606: "This line cannot send SMS. Check the number is SMS-capable in "
           "the Twilio console.",
    21612: "Twilio cannot route a message to that number from this line.",
    30007: "Carrier filtered the message. Usually means the number needs "
           "A2P 10DLC registration -- see the README.",
    30034: "This line is not registered for A2P 10DLC, so US carriers are "
           "rejecting its traffic. See the README.",
    11200: "Twilio could not reach this app's webhook.",
}


class SendError(RuntimeError):
    """Raised when Twilio refuses a message; carries a human-readable reason."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class SendResult:
    sid: str
    status: str


class Gateway(Protocol):
    def validate_signature(
        self, url: str, params: dict[str, str], signature: str | None
    ) -> bool: ...

    async def send(
        self,
        *,
        from_number: str,
        to_number: str,
        body: str,
        media_urls: list[str] | None = None,
        status_callback: str | None = None,
    ) -> SendResult: ...

    async def fetch_media(self, url: str) -> tuple[bytes, str]: ...


class TwilioGateway:
    """The real thing."""

    name = "twilio"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        self._validator = RequestValidator(settings.twilio_auth_token)

    # ------------------------------------------------------------------
    def validate_signature(
        self, url: str, params: dict[str, str], signature: str | None
    ) -> bool:
        if not self._settings.validate_twilio_signature:
            log.warning("Twilio signature validation is DISABLED by configuration")
            return True
        if not signature:
            return False
        return self._validator.validate(url, params, signature)

    # ------------------------------------------------------------------
    async def send(
        self,
        *,
        from_number: str,
        to_number: str,
        body: str,
        media_urls: list[str] | None = None,
        status_callback: str | None = None,
    ) -> SendResult:
        kwargs: dict[str, object] = {"to": to_number, "from_": from_number}
        if body:
            kwargs["body"] = body
        if media_urls:
            kwargs["media_url"] = media_urls
        if status_callback:
            kwargs["status_callback"] = status_callback

        def _create():
            return self._client.messages.create(**kwargs)

        try:
            message = await run_in_threadpool(_create)
        except TwilioRestException as exc:
            friendly = _FRIENDLY_ERRORS.get(exc.code or 0)
            log.error("Twilio rejected message: code=%s msg=%s", exc.code, exc.msg)
            raise SendError(friendly or exc.msg or "Twilio rejected the message.",
                            exc.code) from exc
        return SendResult(sid=message.sid, status=message.status or "queued")

    # ------------------------------------------------------------------
    async def fetch_media(self, url: str) -> tuple[bytes, str]:
        """Download one inbound attachment. Returns (bytes, content_type).

        Twilio's media URLs need account Basic Auth, but they redirect to a
        pre-signed storage URL that must be fetched WITHOUT that header --
        sending both an Authorization header and the signed query params
        makes the storage backend reject the request. So: ask Twilio without
        following the redirect, then follow it bare.
        """
        import httpx

        auth = (self._settings.twilio_account_sid, self._settings.twilio_auth_token)
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            response = await client.get(url, auth=auth)

            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise SendError("Twilio returned a redirect with no location.")
                response = await client.get(location)

            response.raise_for_status()
            content_type = (
                response.headers.get("content-type", "application/octet-stream")
                .split(";")[0]
                .strip()
            )
            return response.content, content_type


class DemoGateway:
    """Stand-in used by demo mode and the test suite.

    Accepts every signature, pretends every send succeeded, and records what
    it was asked to do so tests can assert on it. Never contacts Twilio.
    """

    name = "demo"

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self._counter = 0

    def validate_signature(
        self, url: str, params: dict[str, str], signature: str | None
    ) -> bool:
        return True

    async def send(
        self,
        *,
        from_number: str,
        to_number: str,
        body: str,
        media_urls: list[str] | None = None,
        status_callback: str | None = None,
    ) -> SendResult:
        self._counter += 1
        record = {
            "from": from_number,
            "to": to_number,
            "body": body,
            "media_urls": list(media_urls or []),
            "status_callback": status_callback,
        }
        self.sent.append(record)
        return SendResult(sid=f"SMdemo{self._counter:028d}"[:34], status="sent")

    async def fetch_media(self, url: str) -> tuple[bytes, str]:
        # A 1x1 transparent PNG, so demo MMS has something real to render.
        import base64

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
            "z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
        )
        return png, "image/png"


def build_gateway(settings: Settings) -> Gateway:
    if settings.store_backend == "memory" or not settings.twilio_configured:
        log.warning("Using DemoGateway -- no messages will reach Twilio")
        return DemoGateway()
    return TwilioGateway(settings)
