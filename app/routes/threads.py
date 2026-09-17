"""The human-facing API: lines, conversations, messages.

Every route here sits behind the session guard. Nothing in this module talks
to Twilio directly -- sending goes through app.outbound so the credentials
and the retry/failure bookkeeping stay in one place.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from app.auth import require_session
from app.config import Settings
from app.deps import ConfigDep, GatewayDep, StoreDep
from app.media import MediaError
from app.models import (
    NewThreadRequest,
    ThreadPatchRequest,
    serialise_line,
    serialise_message,
    serialise_thread,
)
from app.outbound import Attachment, send_message
from app.phone import InvalidNumber, normalise
from app.store.base import Store
from app.twilio_client import Gateway

log = logging.getLogger("quietwave.api")

router = APIRouter(
    prefix="/api", tags=["api"], dependencies=[Depends(require_session)]
)


def _parse_after(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'after' is not an ISO timestamp: {raw!r}",
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def _require_thread(store: Store, thread_id: str) -> dict:
    thread = await store.get_thread(thread_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such conversation."
        )
    return thread


# ----------------------------------------------------------------------
# lines
# ----------------------------------------------------------------------
@router.get("/lines")
async def list_lines(store: Store = StoreDep) -> dict:
    lines = await store.list_lines(active_only=True)
    return {"lines": [serialise_line(line) for line in lines]}


# ----------------------------------------------------------------------
# threads
# ----------------------------------------------------------------------
@router.get("/threads")
async def list_threads(store: Store = StoreDep, line_id: str | None = None) -> dict:
    line_ids = [line_id] if line_id else None
    threads = await store.list_threads(line_ids)
    return {"threads": [serialise_thread(t) for t in threads]}


@router.post("/threads", status_code=status.HTTP_201_CREATED)
async def create_thread(
    payload: NewThreadRequest,
    store: Store = StoreDep,
    settings: Settings = ConfigDep,
) -> dict:
    try:
        number = normalise(payload.counterpart_number, settings.default_country_code)
    except InvalidNumber as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if payload.line_id:
        line = await store.get_line(payload.line_id)
        if line is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No such line.",
            )
    else:
        lines = await store.list_lines(active_only=True)
        line = lines[0] if lines else None
        if line is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active line is configured. Add one with "
                "'python -m app.cli add-line'.",
            )

    if number == line["twilio_number"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That is this line's own number.",
        )

    thread = await store.find_thread(line["id"], number)
    if thread is None:
        thread = await store.create_thread(
            line["id"], number, payload.counterpart_label
        )
    elif payload.counterpart_label:
        thread = await store.update_thread(
            thread["id"], counterpart_label=payload.counterpart_label
        )
    return {"thread": serialise_thread(thread)}


@router.patch("/threads/{thread_id}")
async def patch_thread(
    thread_id: str, payload: ThreadPatchRequest, store: Store = StoreDep
) -> dict:
    await _require_thread(store, thread_id)
    label = (payload.counterpart_label or "").strip() or None
    thread = await store.update_thread(thread_id, counterpart_label=label)
    return {"thread": serialise_thread(thread)}


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str, store: Store = StoreDep) -> dict:
    await _require_thread(store, thread_id)
    removed = await store.delete_thread(thread_id)
    return {"deleted": bool(removed)}


@router.post("/threads/{thread_id}/read")
async def mark_read(thread_id: str, store: Store = StoreDep) -> dict:
    await _require_thread(store, thread_id)
    thread = await store.clear_unread(thread_id)
    return {"thread": serialise_thread(thread)}


# ----------------------------------------------------------------------
# messages
# ----------------------------------------------------------------------
@router.get("/threads/{thread_id}/messages")
async def list_messages(
    thread_id: str,
    store: Store = StoreDep,
    after: str | None = None,
    limit: int = 250,
) -> dict:
    await _require_thread(store, thread_id)
    messages = await store.list_messages(
        thread_id, after=_parse_after(after), limit=max(1, min(limit, 500))
    )
    return {"messages": [serialise_message(m) for m in messages]}


@router.post("/threads/{thread_id}/messages", status_code=status.HTTP_201_CREATED)
async def post_message(
    thread_id: str,
    body: str = Form(default=""),
    attachments: list[UploadFile] | None = File(default=None),
    store: Store = StoreDep,
    gateway: Gateway = GatewayDep,
    settings: Settings = ConfigDep,
) -> dict:
    thread = await _require_thread(store, thread_id)

    payload: list[Attachment] = []
    for upload in attachments or []:
        if not upload.filename:
            continue
        data = await upload.read()
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(
                # Numeric literal: Starlette renamed this constant, and
                # the number is stable across every version.
                status_code=413,
                detail=f"{upload.filename} is too large "
                f"({len(data) // 1024}KB); limit is "
                f"{settings.max_upload_bytes // 1024}KB.",
            )
        payload.append(
            Attachment(
                data=data,
                content_type=upload.content_type or "application/octet-stream",
                filename=upload.filename,
            )
        )

    try:
        message = await send_message(
            store=store,
            gateway=gateway,
            settings=settings,
            thread=thread,
            body=body,
            attachments=payload,
        )
    except MediaError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return {"message": serialise_message(message)}
