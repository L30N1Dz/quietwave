"""Sending a message out through Twilio.

Order matters here. The message row is written first with status ``queued``,
so a send that fails still leaves a visible record with a reason attached
rather than vanishing from the thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.ingest import PREVIEW_LENGTH
from app.media import MediaError, build_outbound_entry, public_media_url
from app.store.base import utcnow
from app.twilio_client import SendError

log = logging.getLogger("quietwave.outbound")

MAX_ATTACHMENTS = 10


@dataclass(slots=True)
class Attachment:
    data: bytes
    content_type: str
    filename: str | None = None


async def send_message(
    *,
    store,
    gateway,
    settings: Settings,
    thread: dict[str, Any],
    body: str,
    attachments: list[Attachment] | None = None,
) -> dict[str, Any]:
    """Persist and transmit one outbound message. Returns the stored message."""
    attachments = attachments or []
    body = (body or "").strip()

    if not body and not attachments:
        raise MediaError("nothing to send")
    if len(attachments) > MAX_ATTACHMENTS:
        raise MediaError(
            f"{len(attachments)} attachments; Twilio accepts at most "
            f"{MAX_ATTACHMENTS} per message"
        )

    line = await store.get_line(thread["line_id"])
    if line is None:
        raise MediaError("this conversation's line no longer exists")

    now = utcnow()
    message = await store.insert_message(
        {
            "thread_id": thread["id"],
            "line_id": line["id"],
            "direction": "outbound",
            "body": body,
            "media": [],
            "twilio_sid": None,
            "status": "queued",
            "error": None,
            "created_at": now,
        }
    )

    # Attachments need the message id to decide where on disk they live.
    media_entries: list[dict[str, Any]] = []
    media_urls: list[str] = []
    if attachments:
        if not settings.public_base_url.startswith("https://"):
            await store.update_message(
                message["id"],
                status="failed",
                error="Attachments need a public https PUBLIC_BASE_URL that "
                "Twilio can reach.",
            )
            raise MediaError(
                "Attachments require PUBLIC_BASE_URL to be a public https URL "
                f"Twilio can fetch from; it is currently "
                f"{settings.public_base_url!r}."
            )
        for index, attachment in enumerate(attachments):
            entry = build_outbound_entry(
                settings,
                message["id"],
                index,
                attachment.data,
                attachment.content_type,
                attachment.filename,
            )
            media_entries.append(entry)
            media_urls.append(public_media_url(settings, entry["public_token"]))
        message = await store.update_message(message["id"], media=media_entries)

    try:
        result = await gateway.send(
            from_number=line["twilio_number"],
            to_number=thread["counterpart_number"],
            body=body,
            media_urls=media_urls,
            status_callback=settings.webhook_status_url
            if settings.public_base_url.startswith("https://")
            else None,
        )
    except SendError as exc:
        log.error("send failed for thread %s: %s", thread["id"], exc)
        failed = await store.update_message(
            message["id"], status="failed", error=str(exc)
        )
        await store.bump_thread(
            thread["id"],
            last_message_at=now,
            preview=body[:PREVIEW_LENGTH],
            direction="outbound",
            has_media=bool(media_entries),
        )
        return failed or message

    message = await store.update_message(
        message["id"], twilio_sid=result.sid, status=result.status
    )
    await store.bump_thread(
        thread["id"],
        last_message_at=now,
        preview=body[:PREVIEW_LENGTH],
        direction="outbound",
        has_media=bool(media_entries),
    )
    log.info("sent %s to %s", result.sid, thread["counterpart_number"])
    return message
