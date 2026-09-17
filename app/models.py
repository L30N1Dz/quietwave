"""Request bodies, and the serialisers that decide what the browser may see.

The serialisers are the security boundary for attachments: stored disk paths,
Twilio source URLs and outbound public tokens all exist in the database and
none of them are ever sent to the client.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.phone import pretty
from app.store.base import as_utc


# ----------------------------------------------------------------------
# requests
# ----------------------------------------------------------------------
class LoginRequest(BaseModel):
    passphrase: str = Field(min_length=1, max_length=512)


class NewThreadRequest(BaseModel):
    counterpart_number: str = Field(min_length=1, max_length=32)
    counterpart_label: str | None = Field(default=None, max_length=80)
    line_id: str | None = None


class ThreadPatchRequest(BaseModel):
    counterpart_label: str | None = Field(default=None, max_length=80)


# ----------------------------------------------------------------------
# serialisers
# ----------------------------------------------------------------------
def iso(value: Any) -> str | None:
    value = as_utc(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return None


def serialise_line(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": line["id"],
        "twilio_number": line["twilio_number"],
        "pretty_number": pretty(line["twilio_number"]),
        "label": line.get("label") or "",
        "active": bool(line.get("active", True)),
    }


def serialise_thread(thread: dict[str, Any]) -> dict[str, Any]:
    number = thread["counterpart_number"]
    label = (thread.get("counterpart_label") or "").strip()
    return {
        "id": thread["id"],
        "line_id": thread["line_id"],
        "counterpart_number": number,
        "counterpart_label": label or None,
        "display_name": label or pretty(number),
        "last_message_at": iso(thread.get("last_message_at")),
        "last_message_preview": thread.get("last_message_preview") or "",
        "last_message_direction": thread.get("last_message_direction"),
        "last_message_has_media": bool(thread.get("last_message_has_media")),
        "unread_count": int(thread.get("unread_count") or 0),
    }


def serialise_media(message_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Public view of one attachment.

    Note what is absent: ``path`` (internal disk layout), ``source_url``
    (a Twilio URL needing our account credentials) and ``public_token``
    (the unguessable URL Twilio collects outbound media from).
    """
    index = int(entry.get("index", 0))
    state = entry.get("state", "pending")
    return {
        "index": index,
        "content_type": entry.get("content_type") or "application/octet-stream",
        "state": state,
        "size": entry.get("size"),
        "filename": entry.get("filename"),
        "error": entry.get("error"),
        "url": f"/api/media/{message_id}/{index}" if state == "stored" else None,
    }


def serialise_message(message: dict[str, Any]) -> dict[str, Any]:
    message_id = message["id"]
    media = [serialise_media(message_id, e) for e in message.get("media", [])]
    return {
        "id": message_id,
        "thread_id": message["thread_id"],
        "direction": message["direction"],
        "body": message.get("body") or "",
        "media": media,
        "status": message.get("status") or "unknown",
        "error": message.get("error"),
        "created_at": iso(message.get("created_at")),
    }
