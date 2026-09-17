"""Turning an inbound Twilio webhook into stored history.

Kept out of the route handler so it can be tested directly, and so the
webhook endpoint stays focused on signature checking and returning fast.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.media import pending_inbound_entries
from app.phone import InvalidNumber, normalise
from app.store.base import utcnow

log = logging.getLogger("quietwave.ingest")

PREVIEW_LENGTH = 140


@dataclass(slots=True)
class InboundResult:
    message: dict[str, Any] | None
    thread: dict[str, Any] | None
    ignored_reason: str | None = None
    duplicate: bool = False

    @property
    def stored(self) -> bool:
        return self.message is not None and not self.duplicate


async def handle_inbound(
    form: dict[str, str], *, store, settings: Settings
) -> InboundResult:
    """Persist one inbound message.

    Reads only the fields it needs and ignores everything else, because the
    webhook's parameter set belongs to Twilio and grows over time.
    """
    to_raw = form.get("To") or ""
    from_raw = form.get("From") or ""
    body = form.get("Body") or ""
    sid = form.get("MessageSid") or form.get("SmsMessageSid") or ""

    if not to_raw or not from_raw:
        return InboundResult(None, None, "webhook had no To/From")

    try:
        to_number = normalise(to_raw, settings.default_country_code)
        from_number = normalise(from_raw, settings.default_country_code)
    except InvalidNumber as exc:
        return InboundResult(None, None, f"unparseable number: {exc}")

    # Twilio retries on any non-2xx, so the same SID can legitimately arrive
    # more than once. Storing it twice would show a duplicate message.
    if sid:
        existing = await store.find_message_by_sid(sid)
        if existing is not None:
            log.info("ignoring duplicate delivery of %s", sid)
            thread = await store.get_thread(existing["thread_id"])
            return InboundResult(existing, thread, None, duplicate=True)

    line = await store.get_line_by_number(to_number)
    if line is None:
        return InboundResult(
            None, None, f"no line provisioned for {to_number}"
        )
    if not line.get("active", True):
        return InboundResult(None, None, f"line {to_number} is inactive")

    thread = await store.find_thread(line["id"], from_number)
    if thread is None:
        thread = await store.create_thread(line["id"], from_number)

    media = pending_inbound_entries(form)
    now = utcnow()
    message = await store.insert_message(
        {
            "thread_id": thread["id"],
            "line_id": line["id"],
            "direction": "inbound",
            "body": body,
            "media": media,
            "twilio_sid": sid or None,
            "status": "received",
            "error": None,
            "created_at": now,
        }
    )
    await store.bump_thread(
        thread["id"],
        last_message_at=now,
        preview=body[:PREVIEW_LENGTH],
        direction="inbound",
        unread_delta=1,
        has_media=bool(media),
    )
    log.info(
        "stored inbound %s -> %s (%d attachment(s))",
        from_number,
        to_number,
        len(media),
    )
    return InboundResult(message, thread)


async def handle_status_callback(form: dict[str, str], *, store) -> dict | None:
    """Apply a Twilio delivery receipt to the message it refers to."""
    sid = form.get("MessageSid") or form.get("SmsSid") or ""
    status = form.get("MessageStatus") or form.get("SmsStatus") or ""
    if not sid or not status:
        return None

    fields: dict[str, Any] = {"status": status}
    error_code = form.get("ErrorCode")
    if error_code:
        fields["error"] = f"Twilio error {error_code}"
        if error_code in ("30007", "30034"):
            fields["error"] = (
                f"Twilio error {error_code}: carrier filtered this message. "
                "The line most likely needs A2P 10DLC registration."
            )
    updated = await store.update_message_by_sid(sid, **fields)
    if updated is None:
        log.info("status callback for unknown sid %s", sid)
    return updated
