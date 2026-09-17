"""Session gate, conversation management, and sending."""

from __future__ import annotations

import pytest

PASS = "correct-horse-battery"


# ----------------------------------------------------------------------
# the gate
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/threads"),
        ("get", "/api/lines"),
        ("post", "/api/threads"),
        ("get", "/api/threads/whatever/messages"),
        ("post", "/api/threads/whatever/messages"),
        ("get", "/api/media/whatever/0"),
    ],
)
async def test_api_requires_a_session(client, method, path):
    response = await getattr(client, method)(path)
    assert response.status_code == 401, f"{method} {path} was not guarded"


async def test_wrong_passphrase_is_rejected(client):
    response = await client.post("/auth/login", json={"passphrase": "nope"})
    assert response.status_code == 401
    assert (await client.get("/api/threads")).status_code == 401


async def test_login_then_logout(client):
    assert (await client.post("/auth/login", json={"passphrase": PASS})).status_code == 200
    assert (await client.get("/api/threads")).status_code == 200

    state = (await client.get("/api/session")).json()
    assert state["authenticated"] is True

    await client.post("/auth/logout")
    assert (await client.get("/api/threads")).status_code == 401


async def test_brute_force_is_throttled(client):
    codes = []
    for _ in range(12):
        response = await client.post("/auth/login", json={"passphrase": "wrong"})
        codes.append(response.status_code)
    assert 429 in codes, codes
    # Once throttled, even the right passphrase is refused until it expires.
    assert (await client.post("/auth/login", json={"passphrase": PASS})).status_code == 429


async def test_config_endpoint_leaks_nothing(client, settings):
    payload = (await client.get("/api/config")).json()
    assert payload["theme"] == "quietwave"
    assert payload["authenticated"] is False
    blob = str(payload).lower()
    for secret in (settings.app_passphrase, settings.session_secret):
        assert secret.lower() not in blob


async def test_healthz_is_public(client):
    payload = (await client.get("/healthz")).json()
    assert payload["status"] == "ok"
    assert payload["store_reachable"] is True


# ----------------------------------------------------------------------
# conversations
# ----------------------------------------------------------------------
async def test_create_thread_normalises_the_number(signed_in, line):
    response = await signed_in.post(
        "/api/threads",
        json={"counterpart_number": "(555) 222-0000", "counterpart_label": "NIGHTJAR"},
    )
    assert response.status_code == 201, response.text
    thread = response.json()["thread"]
    assert thread["counterpart_number"] == "+15552220000"
    assert thread["display_name"] == "NIGHTJAR"


async def test_create_thread_is_idempotent(signed_in, line):
    first = await signed_in.post(
        "/api/threads", json={"counterpart_number": "+15552220000"}
    )
    second = await signed_in.post(
        "/api/threads", json={"counterpart_number": "5552220000"}
    )
    assert first.json()["thread"]["id"] == second.json()["thread"]["id"]


async def test_create_thread_rejects_nonsense(signed_in, line):
    response = await signed_in.post(
        "/api/threads", json={"counterpart_number": "not a number"}
    )
    assert response.status_code == 400


async def test_create_thread_rejects_the_lines_own_number(signed_in, line):
    response = await signed_in.post(
        "/api/threads", json={"counterpart_number": line["twilio_number"]}
    )
    assert response.status_code == 400


async def test_create_thread_without_a_line_explains_itself(signed_in):
    response = await signed_in.post(
        "/api/threads", json={"counterpart_number": "+15552220000"}
    )
    assert response.status_code == 400
    assert "add-line" in response.json()["detail"]


async def test_threads_are_listed_newest_first(signed_in, app, line):
    store = app.state.store
    older = await store.create_thread(line["id"], "+15551111111")
    newer = await store.create_thread(line["id"], "+15552222222")
    from datetime import timedelta

    from app.store.base import utcnow

    now = utcnow()
    await store.bump_thread(older["id"], now - timedelta(hours=2), "old", "inbound")
    await store.bump_thread(newer["id"], now, "new", "inbound")

    listed = (await signed_in.get("/api/threads")).json()["threads"]
    assert [t["id"] for t in listed] == [newer["id"], older["id"]]


async def test_rename_and_delete_thread(signed_in, app, thread):
    renamed = await signed_in.patch(
        f"/api/threads/{thread['id']}", json={"counterpart_label": "KESTREL"}
    )
    assert renamed.json()["thread"]["display_name"] == "KESTREL"

    cleared = await signed_in.patch(
        f"/api/threads/{thread['id']}", json={"counterpart_label": "  "}
    )
    assert cleared.json()["thread"]["display_name"] == "(555) 222-0000"

    deleted = await signed_in.delete(f"/api/threads/{thread['id']}")
    assert deleted.json()["deleted"] is True
    assert await app.state.store.get_thread(thread["id"]) is None


async def test_unknown_thread_is_404(signed_in):
    assert (await signed_in.get("/api/threads/nope/messages")).status_code == 404
    assert (await signed_in.post("/api/threads/nope/read")).status_code == 404


async def test_mark_read_clears_the_counter(signed_in, app, line, thread):
    from app.store.base import utcnow

    await app.state.store.bump_thread(
        thread["id"], utcnow(), "hi", "inbound", unread_delta=3
    )
    response = await signed_in.post(f"/api/threads/{thread['id']}/read")
    assert response.json()["thread"]["unread_count"] == 0


# ----------------------------------------------------------------------
# sending
# ----------------------------------------------------------------------
async def test_send_text_reaches_the_gateway(signed_in, app, line, thread):
    response = await signed_in.post(
        f"/api/threads/{thread['id']}/messages", data={"body": "walking, 8am"}
    )
    assert response.status_code == 201, response.text
    message = response.json()["message"]
    assert message["direction"] == "outbound"
    assert message["body"] == "walking, 8am"
    assert message["status"] == "sent"

    sent = app.state.gateway.sent
    assert len(sent) == 1
    assert sent[0]["to"] == "+15552220000"
    assert sent[0]["from"] == "+15551110000"
    assert sent[0]["body"] == "walking, 8am"
    assert sent[0]["status_callback"] == (
        "https://text.example.com/webhooks/twilio/status"
    )


async def test_send_updates_the_thread_preview(signed_in, app, thread):
    await signed_in.post(
        f"/api/threads/{thread['id']}/messages", data={"body": "borrowed*"}
    )
    updated = await app.state.store.get_thread(thread["id"])
    assert updated["last_message_preview"] == "borrowed*"
    assert updated["last_message_direction"] == "outbound"


async def test_empty_message_is_refused(signed_in, thread):
    response = await signed_in.post(
        f"/api/threads/{thread['id']}/messages", data={"body": "   "}
    )
    assert response.status_code == 400


async def test_send_failure_is_recorded_not_lost(signed_in, app, thread):
    """A refused send must still leave a visible message with a reason."""
    from app.twilio_client import SendError

    async def explode(**kwargs):
        raise SendError("That number has replied STOP to this line.", 21610)

    app.state.gateway.send = explode

    response = await signed_in.post(
        f"/api/threads/{thread['id']}/messages", data={"body": "hello?"}
    )
    assert response.status_code == 201
    message = response.json()["message"]
    assert message["status"] == "failed"
    assert "STOP" in message["error"]

    history = (
        await signed_in.get(f"/api/threads/{thread['id']}/messages")
    ).json()["messages"]
    assert len(history) == 1
    assert history[0]["status"] == "failed"


async def test_message_history_supports_an_after_cursor(signed_in, app, thread):
    await signed_in.post(f"/api/threads/{thread['id']}/messages", data={"body": "one"})
    first = (
        await signed_in.get(f"/api/threads/{thread['id']}/messages")
    ).json()["messages"]
    assert len(first) == 1

    await signed_in.post(f"/api/threads/{thread['id']}/messages", data={"body": "two"})
    delta = (
        await signed_in.get(
            f"/api/threads/{thread['id']}/messages",
            params={"after": first[-1]["created_at"]},
        )
    ).json()["messages"]
    assert [m["body"] for m in delta] == ["two"]


async def test_bad_after_cursor_is_a_400(signed_in, thread):
    response = await signed_in.get(
        f"/api/threads/{thread['id']}/messages", params={"after": "yesterday-ish"}
    )
    assert response.status_code == 400
