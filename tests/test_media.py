"""Attachment storage, the public outbound token, and what leaks to clients."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.media import (
    MediaError,
    build_outbound_entry,
    extension_for,
    pending_inbound_entries,
    resolve_stored_path,
    token_is_live,
)
from app.models import serialise_media, serialise_message

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ----------------------------------------------------------------------
# what the browser is allowed to see
# ----------------------------------------------------------------------
def test_serialiser_hides_paths_tokens_and_twilio_urls():
    entry = {
        "index": 0,
        "content_type": "image/jpeg",
        "state": "stored",
        "path": "abc123/0.jpg",
        "size": 2048,
        "public_token": "super-secret-token",
        "source_url": "https://api.twilio.com/media/ME1",
        "expires_at": datetime.now(timezone.utc),
    }
    public = serialise_media("abc123", entry)

    assert public["url"] == "/api/media/abc123/0"
    for forbidden in ("path", "public_token", "source_url", "expires_at"):
        assert forbidden not in public, f"{forbidden} leaked to the client"

    blob = str(public)
    assert "super-secret-token" not in blob
    assert "api.twilio.com" not in blob


def test_pending_attachment_has_no_url_yet():
    entry = {"index": 1, "content_type": "image/png", "state": "pending"}
    assert serialise_media("m1", entry)["url"] is None


def test_message_serialiser_omits_twilio_sid():
    message = {
        "id": "m1",
        "thread_id": "t1",
        "direction": "outbound",
        "body": "hi",
        "media": [],
        "twilio_sid": "SM0123456789",
        "status": "sent",
        "error": None,
        "created_at": datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
    }
    public = serialise_message(message)
    assert "twilio_sid" not in public
    assert public["created_at"] == "2026-09-17T12:00:00Z"


# ----------------------------------------------------------------------
# inbound parsing
# ----------------------------------------------------------------------
def test_pending_inbound_entries_reads_all_indices():
    form = {
        "NumMedia": "3",
        "MediaUrl0": "https://api.twilio.com/0",
        "MediaContentType0": "image/jpeg",
        "MediaUrl1": "https://api.twilio.com/1",
        "MediaContentType1": "video/mp4",
        "MediaUrl2": "https://api.twilio.com/2",
        "MediaContentType2": "image/gif",
    }
    entries = pending_inbound_entries(form)
    assert [e["index"] for e in entries] == [0, 1, 2]
    assert [e["content_type"] for e in entries] == [
        "image/jpeg",
        "video/mp4",
        "image/gif",
    ]
    assert all(e["state"] == "pending" for e in entries)


def test_pending_inbound_entries_survives_a_gap():
    """A missing index must not silently truncate the rest."""
    form = {
        "NumMedia": "3",
        "MediaUrl0": "https://api.twilio.com/0",
        "MediaUrl2": "https://api.twilio.com/2",
    }
    assert len(pending_inbound_entries(form)) == 2


def test_pending_inbound_entries_handles_garbage_nummedia():
    assert pending_inbound_entries({"NumMedia": "banana"}) == []
    assert pending_inbound_entries({}) == []


@pytest.mark.parametrize(
    "content_type,expected",
    [
        ("image/jpeg", ".jpg"),
        ("image/png", ".png"),
        ("video/mp4", ".mp4"),
        ("image/jpeg; charset=binary", ".jpg"),
        ("IMAGE/PNG", ".png"),
        ("application/x-unheard-of", ".bin"),
        ("", ".bin"),
    ],
)
def test_extension_mapping(content_type, expected):
    assert extension_for(content_type) == expected


# ----------------------------------------------------------------------
# outbound storage
# ----------------------------------------------------------------------
def test_outbound_entry_gets_an_unguessable_expiring_token(settings):
    entry = build_outbound_entry(settings, "msg1", 0, PNG, "image/png", "cat.png")
    assert len(entry["public_token"]) >= 32
    assert entry["state"] == "stored"
    assert entry["expires_at"] > datetime.now(timezone.utc)
    assert (settings.media_root / entry["path"]).read_bytes() == PNG


def test_outbound_rejects_unsupported_types(settings):
    with pytest.raises(MediaError, match="not supported"):
        build_outbound_entry(settings, "msg1", 0, b"MZ...", "application/x-msdownload")


def test_outbound_rejects_oversized_attachments(settings):
    too_big = b"\x00" * (settings.max_upload_bytes + 1)
    with pytest.raises(MediaError, match="limit is"):
        build_outbound_entry(settings, "msg1", 0, too_big, "image/png")


def test_expired_token_is_dead():
    past = {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
    future = {"expires_at": datetime.now(timezone.utc) + timedelta(hours=1)}
    assert token_is_live(past) is False
    assert token_is_live(future) is True
    assert token_is_live({}) is True


# ----------------------------------------------------------------------
# path safety
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "escape",
    ["../../etc/passwd", "..\\..\\windows\\system32\\config", "/etc/passwd"],
)
def test_stored_paths_cannot_escape_the_media_root(settings, escape):
    settings.media_root.mkdir(parents=True, exist_ok=True)
    with pytest.raises(MediaError):
        resolve_stored_path(settings, escape)


def test_unsafe_message_ids_are_refused(settings):
    with pytest.raises(MediaError, match="unsafe message id"):
        build_outbound_entry(settings, "../../evil", 0, PNG, "image/png")


# ----------------------------------------------------------------------
# end to end through HTTP
# ----------------------------------------------------------------------
async def test_outbound_mms_is_fetchable_by_twilio_and_not_by_strangers(
    signed_in, app, settings, thread
):
    response = await signed_in.post(
        f"/api/threads/{thread['id']}/messages",
        data={"body": "look at this"},
        files={"attachments": ("cat.png", PNG, "image/png")},
    )
    assert response.status_code == 201, response.text
    message = response.json()["message"]
    assert message["media"][0]["url"] == f"/api/media/{message['id']}/0"

    # Twilio was handed a public /m/<token> URL, never the API path.
    handed_to_twilio = app.state.gateway.sent[0]["media_urls"]
    assert len(handed_to_twilio) == 1
    assert handed_to_twilio[0].startswith("https://text.example.com/m/")
    assert "/api/media" not in handed_to_twilio[0]

    # That public URL works without a session, because Twilio cannot log in.
    token = handed_to_twilio[0].rsplit("/", 1)[-1]
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://text.example.com"
    ) as anonymous:
        public = await anonymous.get(f"/m/{token}")
        assert public.status_code == 200
        assert public.content == PNG

        # ...but the session-guarded route still refuses them.
        guarded = await anonymous.get(f"/api/media/{message['id']}/0")
        assert guarded.status_code == 401

    # The owner can fetch it through the guarded route.
    owner = await signed_in.get(f"/api/media/{message['id']}/0")
    assert owner.status_code == 200
    assert owner.content == PNG


async def test_unknown_public_token_is_404(client, app):
    response = await client.get("/m/not-a-real-token")
    assert response.status_code == 404


async def test_expired_public_token_is_410(signed_in, app, thread):
    await signed_in.post(
        f"/api/threads/{thread['id']}/messages",
        files={"attachments": ("cat.png", PNG, "image/png")},
        data={"body": "here"},
    )
    token = app.state.gateway.sent[0]["media_urls"][0].rsplit("/", 1)[-1]
    found = await app.state.store.find_by_media_token(token)
    message, index = found
    await app.state.store.patch_media(
        message["id"],
        index,
        {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
    )
    assert (await signed_in.get(f"/m/{token}")).status_code == 410


async def test_oversized_upload_is_refused(signed_in, settings, thread):
    huge = b"\x00" * (settings.max_upload_bytes + 1024)
    response = await signed_in.post(
        f"/api/threads/{thread['id']}/messages",
        data={"body": "big"},
        files={"attachments": ("huge.png", huge, "image/png")},
    )
    assert response.status_code == 413


async def test_unsupported_attachment_type_is_refused(signed_in, thread):
    response = await signed_in.post(
        f"/api/threads/{thread['id']}/messages",
        data={"body": "exe"},
        files={"attachments": ("bad.exe", b"MZ", "application/x-msdownload")},
    )
    assert response.status_code == 400
