"""Twilio's two inbound endpoints.

Both are authenticated by Twilio's own signature rather than by session, and
both return quickly: attachment downloads are handed to a background task so
Twilio is not kept waiting (it gives up after about 15 seconds).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Request, Response, status

from app.config import Settings
from app.deps import ConfigDep, GatewayDep, StoreDep
from app.ingest import handle_inbound, handle_status_callback
from app.media import download_inbound_media
from app.store.base import Store
from app.twilio_client import Gateway

log = logging.getLogger("quietwave.webhooks")

router = APIRouter(prefix="/webhooks/twilio", tags=["webhooks"])


async def _form_as_dict(request: Request) -> dict[str, str]:
    form = await request.form()
    return {key: value for key, value in form.items() if isinstance(value, str)}


@router.post("/sms")
async def inbound_sms(
    request: Request,
    background: BackgroundTasks,
    store: Store = StoreDep,
    gateway: Gateway = GatewayDep,
    settings: Settings = ConfigDep,
) -> Response:
    form = await _form_as_dict(request)

    # The signed URL is built from configuration, never from this request:
    # behind Cloudflare Tunnel the request's own scheme/host/port is not what
    # Twilio signed against.
    if not gateway.validate_signature(
        settings.webhook_sms_url, form, request.headers.get("X-Twilio-Signature")
    ):
        log.warning(
            "rejected inbound webhook with bad signature (expected url %s)",
            settings.webhook_sms_url,
        )
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    result = await handle_inbound(form, store=store, settings=settings)

    if result.ignored_reason:
        # Still a 2xx: this payload is not going to succeed on a retry, and
        # making Twilio redeliver it forever helps nobody.
        log.info("ignored inbound message: %s", result.ignored_reason)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if result.stored and result.message and result.message.get("media"):
        background.add_task(
            download_inbound_media,
            gateway=gateway,
            store=store,
            settings=settings,
            message_id=result.message["id"],
        )

    # Empty 204, deliberately no TwiML: Twilio must not auto-reply.
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/status")
async def delivery_status(
    request: Request,
    store: Store = StoreDep,
    gateway: Gateway = GatewayDep,
    settings: Settings = ConfigDep,
) -> Response:
    form = await _form_as_dict(request)

    if not gateway.validate_signature(
        settings.webhook_status_url, form, request.headers.get("X-Twilio-Signature")
    ):
        log.warning("rejected status callback with bad signature")
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    await handle_status_callback(form, store=store)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
