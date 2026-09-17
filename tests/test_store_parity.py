"""Both backends must behave identically.

Most of the suite runs against MemoryStore because it needs no server. That
is only trustworthy if MongoStore agrees, so every test here runs twice --
once per backend -- with the Mongo pass skipped unless a test server is
configured:

    QUIETWAVE_TEST_MONGO_URI=mongodb://localhost:27017 uv run pytest

The Mongo pass uses its own throwaway database and drops it afterwards.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio

from app.store.base import utcnow
from app.store.memory import MemoryStore
from app.store.mongo import MongoStore

TEST_MONGO_URI = os.environ.get("QUIETWAVE_TEST_MONGO_URI")


@pytest_asyncio.fixture(params=["memory", "mongo"])
async def store(request):
    if request.param == "memory":
        backend = MemoryStore()
        await backend.startup()
        yield backend
        await backend.shutdown()
        return

    if not TEST_MONGO_URI:
        pytest.skip("set QUIETWAVE_TEST_MONGO_URI to run the MongoDB parity pass")

    db_name = f"quietwave_test_{uuid.uuid4().hex[:10]}"
    backend = MongoStore(TEST_MONGO_URI, db_name)
    await backend.startup()
    try:
        yield backend
    finally:
        # Drop the scratch database; never touch anything else on the server.
        await backend._client.drop_database(db_name)
        await backend.shutdown()


# ----------------------------------------------------------------------
async def test_ping(store):
    assert await store.ping() is True


async def test_line_roundtrip(store):
    line = await store.create_line("+15551110000", "Hers", True)
    assert line["id"]
    assert line["twilio_number"] == "+15551110000"

    assert (await store.get_line(line["id"]))["label"] == "Hers"
    assert (await store.get_line_by_number("+15551110000"))["id"] == line["id"]
    assert [l["id"] for l in await store.list_lines()] == [line["id"]]


async def test_duplicate_line_returns_the_existing_one(store):
    first = await store.create_line("+15551110000", "One", True)
    second = await store.create_line("+15551110000", "Two", True)
    assert first["id"] == second["id"]
    assert len(await store.list_lines(active_only=False)) == 1


async def test_inactive_lines_are_filtered(store):
    line = await store.create_line("+15551110000", "Hers", True)
    await store.update_line(line["id"], active=False)
    assert await store.list_lines(active_only=True) == []
    assert len(await store.list_lines(active_only=False)) == 1


async def test_unknown_ids_return_none_not_errors(store):
    """Ids arrive from URLs, so garbage must not raise."""
    assert await store.get_line("not-an-id") is None
    assert await store.get_thread("not-an-id") is None
    assert await store.get_message("not-an-id") is None
    assert await store.update_thread("not-an-id", counterpart_label="x") is None
    assert await store.delete_thread("not-an-id") == 0


async def test_thread_uniqueness_per_line_and_counterpart(store):
    line = await store.create_line("+15551110000", "Hers", True)
    first = await store.create_thread(line["id"], "+15552220000")
    second = await store.create_thread(line["id"], "+15552220000")
    assert first["id"] == second["id"]
    assert len(await store.list_threads()) == 1


async def test_same_counterpart_on_two_lines_are_separate_threads(store):
    one = await store.create_line("+15551110000", "One", True)
    two = await store.create_line("+15551110001", "Two", True)
    a = await store.create_thread(one["id"], "+15552220000")
    b = await store.create_thread(two["id"], "+15552220000")
    assert a["id"] != b["id"]
    assert len(await store.list_threads()) == 2
    assert [t["id"] for t in await store.list_threads([one["id"]])] == [a["id"]]


async def test_threads_sort_newest_first(store):
    line = await store.create_line("+15551110000", "Hers", True)
    old = await store.create_thread(line["id"], "+15551111111")
    new = await store.create_thread(line["id"], "+15552222222")
    now = utcnow()
    await store.bump_thread(old["id"], now - timedelta(hours=1), "old", "inbound")
    await store.bump_thread(new["id"], now, "new", "inbound")
    assert [t["id"] for t in await store.list_threads()] == [new["id"], old["id"]]


async def test_unread_counter_increments_and_clears(store):
    line = await store.create_line("+15551110000", "Hers", True)
    thread = await store.create_thread(line["id"], "+15552220000")
    now = utcnow()
    for _ in range(3):
        await store.bump_thread(thread["id"], now, "hi", "inbound", unread_delta=1)
    assert (await store.get_thread(thread["id"]))["unread_count"] == 3
    await store.clear_unread(thread["id"])
    assert (await store.get_thread(thread["id"]))["unread_count"] == 0


async def test_bump_records_preview_direction_and_media_flag(store):
    line = await store.create_line("+15551110000", "Hers", True)
    thread = await store.create_thread(line["id"], "+15552220000")
    await store.bump_thread(
        thread["id"], utcnow(), "a picture", "outbound", has_media=True
    )
    updated = await store.get_thread(thread["id"])
    assert updated["last_message_preview"] == "a picture"
    assert updated["last_message_direction"] == "outbound"
    assert updated["last_message_has_media"] is True


# ----------------------------------------------------------------------
async def _seed_message(store, thread, line, **overrides):
    doc = {
        "thread_id": thread["id"],
        "line_id": line["id"],
        "direction": "inbound",
        "body": "hello",
        "media": [],
        "twilio_sid": None,
        "status": "received",
        "error": None,
    }
    doc.update(overrides)
    return await store.insert_message(doc)


async def test_messages_sort_oldest_first_and_honour_after(store):
    line = await store.create_line("+15551110000", "Hers", True)
    thread = await store.create_thread(line["id"], "+15552220000")
    base = utcnow()
    for offset, body in enumerate(["one", "two", "three"]):
        await _seed_message(
            store, thread, line, body=body,
            created_at=base + timedelta(seconds=offset),
        )
    messages = await store.list_messages(thread["id"])
    assert [m["body"] for m in messages] == ["one", "two", "three"]

    later = await store.list_messages(thread["id"], after=messages[0]["created_at"])
    assert [m["body"] for m in later] == ["two", "three"]


async def test_message_limit_is_respected(store):
    line = await store.create_line("+15551110000", "Hers", True)
    thread = await store.create_thread(line["id"], "+15552220000")
    base = utcnow()
    for offset in range(10):
        await _seed_message(
            store, thread, line, body=str(offset),
            created_at=base + timedelta(seconds=offset),
        )
    assert len(await store.list_messages(thread["id"], limit=4)) == 4


async def test_find_and_update_message_by_sid(store):
    line = await store.create_line("+15551110000", "Hers", True)
    thread = await store.create_thread(line["id"], "+15552220000")
    await _seed_message(store, thread, line, twilio_sid="SM123", status="queued")

    assert (await store.find_message_by_sid("SM123"))["status"] == "queued"
    assert await store.find_message_by_sid("SMnope") is None

    updated = await store.update_message_by_sid("SM123", status="delivered")
    assert updated["status"] == "delivered"
    assert await store.update_message_by_sid("SMnope", status="delivered") is None


async def test_patch_media_touches_only_its_own_index(store):
    """Sibling attachments download concurrently and must not clobber."""
    line = await store.create_line("+15551110000", "Hers", True)
    thread = await store.create_thread(line["id"], "+15552220000")
    message = await _seed_message(
        store, thread, line,
        media=[
            {"index": 0, "content_type": "image/jpeg", "state": "pending"},
            {"index": 1, "content_type": "image/png", "state": "pending"},
        ],
    )
    await store.patch_media(message["id"], 0, {"state": "stored", "path": "a/0.jpg"})
    await store.patch_media(message["id"], 1, {"state": "failed", "error": "boom"})

    media = (await store.get_message(message["id"]))["media"]
    by_index = {int(entry["index"]): entry for entry in media}
    assert by_index[0]["state"] == "stored"
    assert by_index[0]["path"] == "a/0.jpg"
    assert by_index[1]["state"] == "failed"
    assert by_index[1]["error"] == "boom"


async def test_find_by_media_token(store):
    line = await store.create_line("+15551110000", "Hers", True)
    thread = await store.create_thread(line["id"], "+15552220000")
    message = await _seed_message(
        store, thread, line,
        direction="outbound",
        media=[
            {"index": 0, "state": "stored", "public_token": "tok-zero"},
            {"index": 1, "state": "stored", "public_token": "tok-one"},
        ],
    )
    found = await store.find_by_media_token("tok-one")
    assert found is not None
    doc, index = found
    assert doc["id"] == message["id"]
    assert index == 1
    assert await store.find_by_media_token("tok-missing") is None


async def test_deleting_a_thread_removes_its_messages(store):
    line = await store.create_line("+15551110000", "Hers", True)
    keep = await store.create_thread(line["id"], "+15551111111")
    drop = await store.create_thread(line["id"], "+15552222222")
    await _seed_message(store, keep, line, body="keep me")
    await _seed_message(store, drop, line, body="delete me")

    assert await store.delete_thread(drop["id"]) == 1
    assert await store.get_thread(drop["id"]) is None
    assert await store.list_messages(drop["id"]) == []
    assert [m["body"] for m in await store.list_messages(keep["id"])] == ["keep me"]


async def test_timestamps_come_back_timezone_aware(store):
    """Mongo returns naive UTC; comparisons elsewhere assume aware datetimes."""
    line = await store.create_line("+15551110000", "Hers", True)
    thread = await store.create_thread(line["id"], "+15552220000")
    message = await _seed_message(store, thread, line)

    for doc, field in (
        (await store.get_line(line["id"]), "created_at"),
        (await store.get_thread(thread["id"]), "last_message_at"),
        (await store.get_message(message["id"]), "created_at"),
    ):
        assert doc[field].tzinfo is not None, f"{field} came back naive"
