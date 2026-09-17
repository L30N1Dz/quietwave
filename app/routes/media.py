"""Serving attachments.

Two routes with deliberately different guards:

* ``/api/media/...`` is for the browser and requires a session.
* ``/m/{token}`` is public, because Twilio has to fetch outbound attachments
  and cannot authenticate. Its safety comes from a 32-byte random token that
  expires, and from the fact that the token is never sent to the client.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.auth import require_session
from app.config import Settings
from app.deps import ConfigDep, StoreDep
from app.media import MediaError, resolve_stored_path, token_is_live
from app.store.base import Store

log = logging.getLogger("quietwave.media")

router = APIRouter()


def _entry_for(message: dict, index: int) -> dict:
    for entry in message.get("media", []):
        if int(entry.get("index", -1)) == index:
            return entry
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="No such attachment."
    )


@router.get(
    "/api/media/{message_id}/{index}",
    dependencies=[Depends(require_session)],
    tags=["api"],
)
async def stored_media(
    message_id: str,
    index: int,
    store: Store = StoreDep,
    settings: Settings = ConfigDep,
) -> FileResponse:
    message = await store.get_message(message_id)
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such message."
        )
    entry = _entry_for(message, index)

    if entry.get("state") != "stored" or not entry.get("path"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Attachment is {entry.get('state', 'unavailable')}.",
        )
    try:
        path = resolve_stored_path(settings, entry["path"])
    except MediaError as exc:
        log.error("stored attachment missing: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Attachment is gone."
        ) from exc

    return FileResponse(
        path,
        media_type=entry.get("content_type") or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/m/{token}", tags=["public"], include_in_schema=False)
async def public_outbound_media(
    token: str, store: Store = StoreDep, settings: Settings = ConfigDep
) -> FileResponse:
    """Where Twilio collects an outbound attachment. Unauthenticated by design."""
    found = await store.find_by_media_token(token)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    message, index = found
    entry = _entry_for(message, index)

    if not token_is_live(entry):
        log.info("expired outbound media token used")
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Expired.")
    try:
        path = resolve_stored_path(settings, entry["path"])
    except MediaError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
        ) from exc

    return FileResponse(
        path,
        media_type=entry.get("content_type") or "application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )
