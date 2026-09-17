"""Inbound webhook behaviour: persistence, idempotency, and attachments."""

from __future__ import annotations

import pytest

INBOUND = {
    "MessageSid": "SM00000000000000000000000000000001",
    "AccountSid": "AC00000000000000000000000000000001",
    "From": "+15552220000",
    "To": "+15551110000",
    "Body": "you up",
    "NumMedia": "0",
}


async def test_inbound_creates_thread_and_message(client, app, line):
    response = await client.post("/webhooks/twilio/sms", data=INBOUND)
    assert response.status_code == 204
    # Deliberately no TwiML body -- Twilio must not auto-reply.
    assert response.content == b""

    threads = await app.state.store.list_threads()
    assert len(threads) == 1
    assert threads[0]["counterpart_number"] == "+15552220000"
    assert threads[0]["unread_count"] == 1
    assert threads[0]["last_message_preview"] == "you up"

    messages = await app.state.store.list_messages(threads[0]["id"])
    assert [m["body"] for m in messages] == ["you up"]
    assert messages[0]["direction"] == "inbound"
    assert messages[0]["status"] == "received"


async def test_inbound_reuses_existing_thread(client, app, line, thread):
    await client.post("/webhooks/twilio/sms", data=INBOUND)
    await client.post(
        "/webhooks/twilio/sms",
        data=dict(INBOUND, MessageSid="SM2", Body="still there?"),
    )
    threads = await app.state.store.list_threads()
    assert len(threads) == 1
    assert threads[0]["id"] == thread["id"]
    assert threads[0]["unread_count"] == 2
    messages = await app.state.store.list_messages(thread["id"])
    assert len(messages) == 2


async def test_duplicate_sid_is_not_stored_twice(client, app, line):
    """Twilio retries on non-2xx; the same delivery must not duplicate."""
    await client.post("/webhooks/twilio/sms", data=INBOUND)
    await client.post("/webhooks/twilio/sms", data=INBOUND)

    threads = await app.state.store.list_threads()
    messages = await app.state.store.list_messages(threads[0]["id"])
    assert len(messages) == 1
    assert threads[0]["unread_count"] == 1


async def test_unprovisioned_number_is_ignored_but_acknowledged(client, app):
    """No line for this To -- ack it anyway so Twilio stops retrying."""
    response = await client.post(
        "/webhooks/twilio/sms", data=dict(INBOUND, To="+19998887777")
    )
    assert response.status_code == 204
    assert await app.state.store.list_threads() == []


async def test_inactive_line_is_ignored(client, app):
    store = app.state.store
    dormant = await store.create_line("+15551110000", "Dormant", True)
    await store.update_line(dormant["id"], active=False)

    response = await client.post("/webhooks/twilio/sms", data=INBOUND)
    assert response.status_code == 204
    assert await store.list_threads() == []


async def test_malformed_payload_is_ignored(client, app, line):
    response = await client.post("/webhooks/twilio/sms", data={"Body": "orphan"})
    assert response.status_code == 204
    assert await app.state.store.list_threads() == []


async def test_unknown_future_fields_do_not_break_ingest(client, app, line):
    """Twilio owns the webhook's field set; extra fields must be ignored."""
    payload = dict(
        INBOUND,
        SomeFutureTwilioField="surprise",
        AnotherOne="42",
        MessagingServiceSid="MG123",
    )
    response = await client.post("/webhooks/twilio/sms", data=payload)
    assert response.status_code == 204
    threads = await app.state.store.list_threads()
    assert len(threads) == 1


async def test_inbound_mms_downloads_media(client, app, line, settings):
    payload = dict(
        INBOUND,
        Body="look",
        NumMedia="2",
        MediaUrl0="https://api.twilio.com/media/ME1",
        MediaContentType0="image/jpeg",
        MediaUrl1="https://api.twilio.com/media/ME2",
        MediaContentType1="image/png",
    )
    response = await client.post("/webhooks/twilio/sms", data=payload)
    assert response.status_code == 204

    threads = await app.state.store.list_threads()
    assert threads[0]["last_message_has_media"] is True
    messages = await app.state.store.list_messages(threads[0]["id"])
    media = messages[0]["media"]
    assert len(media) == 2

    # The background task should have stored both and dropped the Twilio URLs.
    for entry in media:
        assert entry["state"] == "stored", entry
        assert entry["path"], entry
        assert entry["source_url"] is None
        assert (settings.media_root / entry["path"]).is_file()


async def test_media_urls_without_nummedia_are_still_collected(client, app, line):
    """Do not trust NumMedia alone; read what is actually present."""
    payload = dict(
        INBOUND,
        NumMedia="0",
        MediaUrl0="https://api.twilio.com/media/ME1",
        MediaContentType0="image/jpeg",
    )
    await client.post("/webhooks/twilio/sms", data=payload)
    threads = await app.state.store.list_threads()
    messages = await app.state.store.list_messages(threads[0]["id"])
    assert len(messages[0]["media"]) == 1


async def test_status_callback_updates_delivery_state(client, app, line, thread):
    store = app.state.store
    message = await store.insert_message(
        {
            "thread_id": thread["id"],
            "line_id": line["id"],
            "direction": "outbound",
            "body": "sent thing",
            "media": [],
            "twilio_sid": "SMoutbound1",
            "status": "queued",
            "error": None,
        }
    )
    response = await client.post(
        "/webhooks/twilio/status",
        data={"MessageSid": "SMoutbound1", "MessageStatus": "delivered"},
    )
    assert response.status_code == 204
    updated = await store.get_message(message["id"])
    assert updated["status"] == "delivered"


async def test_status_callback_explains_carrier_filtering(client, app, line, thread):
    store = app.state.store
    message = await store.insert_message(
        {
            "thread_id": thread["id"],
            "line_id": line["id"],
            "direction": "outbound",
            "body": "filtered thing",
            "media": [],
            "twilio_sid": "SMfiltered",
            "status": "sent",
            "error": None,
        }
    )
    await client.post(
        "/webhooks/twilio/status",
        data={
            "MessageSid": "SMfiltered",
            "MessageStatus": "undelivered",
            "ErrorCode": "30034",
        },
    )
    updated = await store.get_message(message["id"])
    assert updated["status"] == "undelivered"
    assert "10DLC" in updated["error"]


async def test_status_callback_for_unknown_sid_is_harmless(client, app, line):
    response = await client.post(
        "/webhooks/twilio/status",
        data={"MessageSid": "SMneverseen", "MessageStatus": "delivered"},
    )
    assert response.status_code == 204


async def test_webhooks_need_no_session(client, app, line):
    """The webhook is authenticated by signature, not by the UI session."""
    response = await client.post("/webhooks/twilio/sms", data=INBOUND)
    assert response.status_code == 204
