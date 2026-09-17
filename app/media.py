"""Attachment handling, in both directions.

Inbound: Twilio's MediaUrl values need account credentials to fetch, so they
can never be handed to a browser. Each attachment is downloaded once on
receipt, written to disk, and afterwards served from this app's own
session-guarded route.

Outbound: Twilio fetches attachments over the public internet and cannot log
in, so an outbound attachment gets an unguessable, expiring public URL. That
token is the only thing ever given to Twilio.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.store.base import as_utc

log = logging.getLogger("quietwave.media")

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/bmp": ".bmp",
    "video/mp4": ".mp4",
    "video/3gpp": ".3gp",
    "video/quicktime": ".mov",
    "audio/mpeg": ".mp3",
    "audio/amr": ".amr",
    "audio/ogg": ".ogg",
    "text/vcard": ".vcf",
    "text/x-vcard": ".vcf",
    "text/plain": ".txt",
    "application/pdf": ".pdf",
}

# What this app will accept as an outbound attachment. Twilio's MMS support is
# widest for these; anything else is likely to be silently dropped by carriers.
OUTBOUND_ALLOWED = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
    "video/mp4",
    "video/3gpp",
    "application/pdf",
}


class MediaError(RuntimeError):
    """Attachment could not be accepted or stored."""


def extension_for(content_type: str) -> str:
    return _EXTENSIONS.get((content_type or "").lower().split(";")[0].strip(), ".bin")


def _message_dir(settings: Settings, message_id: str) -> Path:
    if not _SAFE_ID.match(message_id):
        raise MediaError(f"refusing unsafe message id {message_id!r}")
    return Path(settings.media_root) / message_id


def resolve_stored_path(settings: Settings, relative: str) -> Path:
    """Map a stored relative path to an absolute one, refusing escapes."""
    root = Path(settings.media_root).resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise MediaError(f"path {relative!r} escapes the media root")
    if not candidate.is_file():
        raise MediaError(f"no stored file at {relative!r}")
    return candidate


def write_attachment(
    settings: Settings,
    message_id: str,
    index: int,
    data: bytes,
    content_type: str,
) -> tuple[str, int]:
    """Write bytes to disk. Returns (path relative to media root, size)."""
    directory = _message_dir(settings, message_id)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{int(index)}{extension_for(content_type)}"
    target = directory / filename
    target.write_bytes(data)
    return f"{message_id}/{filename}", len(data)


# ----------------------------------------------------------------------
# inbound
# ----------------------------------------------------------------------
def pending_inbound_entries(form: dict[str, str]) -> list[dict[str, Any]]:
    """Read MediaUrl0..N / MediaContentType0..N out of a webhook payload.

    Driven by NumMedia but tolerant of it being absent or wrong, because the
    webhook's field set is Twilio's to change, not ours to assume.
    """
    entries: list[dict[str, Any]] = []
    try:
        declared = int(form.get("NumMedia", "0") or "0")
    except ValueError:
        declared = 0

    index = 0
    while True:
        url = form.get(f"MediaUrl{index}")
        if not url:
            # Keep going while Twilio still claims there are more to come.
            if index < declared:
                index += 1
                continue
            break
        entries.append(
            {
                "index": index,
                "content_type": form.get(
                    f"MediaContentType{index}", "application/octet-stream"
                ),
                "state": "pending",
                "path": None,
                "size": None,
                "error": None,
                "source_url": url,
            }
        )
        index += 1
        if index > 64:  # sanity stop
            break
    return entries


async def download_inbound_media(
    *, gateway, store, settings: Settings, message_id: str
) -> None:
    """Background task: fetch every pending attachment for one message."""
    message = await store.get_message(message_id)
    if message is None:
        return
    for entry in message.get("media", []):
        if entry.get("state") != "pending" or not entry.get("source_url"):
            continue
        index = int(entry["index"])
        try:
            data, content_type = await gateway.fetch_media(entry["source_url"])
            path, size = write_attachment(
                settings, message_id, index, data, content_type or entry["content_type"]
            )
            await store.patch_media(
                message_id,
                index,
                {
                    "state": "stored",
                    "path": path,
                    "size": size,
                    "content_type": content_type or entry["content_type"],
                    "source_url": None,  # no longer needed; do not keep it around
                },
            )
            log.info("stored attachment %s[%s] (%s bytes)", message_id, index, size)
        except Exception as exc:  # one bad attachment must not lose the rest
            log.exception("failed to store attachment %s[%s]", message_id, index)
            await store.patch_media(
                message_id, index, {"state": "failed", "error": str(exc)[:300]}
            )


# ----------------------------------------------------------------------
# outbound
# ----------------------------------------------------------------------
def build_outbound_entry(
    settings: Settings,
    message_id: str,
    index: int,
    data: bytes,
    content_type: str,
    filename: str | None = None,
) -> dict[str, Any]:
    """Store an outgoing attachment and mint its one-time public token."""
    normalised = (content_type or "").lower().split(";")[0].strip()
    if normalised not in OUTBOUND_ALLOWED:
        raise MediaError(f"attachment type {normalised or 'unknown'} is not supported")
    if len(data) > settings.max_upload_bytes:
        raise MediaError(
            f"attachment is {len(data) // 1024}KB; the limit is "
            f"{settings.max_upload_bytes // 1024}KB"
        )
    path, size = write_attachment(settings, message_id, index, data, normalised)
    return {
        "index": index,
        "content_type": normalised,
        "state": "stored",
        "path": path,
        "size": size,
        "error": None,
        "filename": filename,
        "public_token": secrets.token_urlsafe(32),
        "expires_at": datetime.now(timezone.utc)
        + timedelta(hours=settings.outbound_media_ttl_hours),
    }


def public_media_url(settings: Settings, token: str) -> str:
    """The URL handed to Twilio so it can collect an outbound attachment."""
    return f"{settings.public_base_url}/m/{token}"


def token_is_live(entry: dict[str, Any]) -> bool:
    expires_at = as_utc(entry.get("expires_at"))
    if expires_at is None:
        return True
    return datetime.now(timezone.utc) < expires_at
