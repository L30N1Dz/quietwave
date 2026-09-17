"""Fake traffic for demo mode.

Exists so the interface -- and all four themes -- can be looked at without a
Twilio account, a MongoDB, or a real conversation to eavesdrop on. Only ever
writes to the in-memory store.
"""

from __future__ import annotations

from datetime import timedelta

from app.store.base import utcnow

_SCRIPT = [
    ("inbound", "did you get the history sheet", 0),
    ("outbound", "yeah mrs patel put it on the portal", 2),
    ("inbound", "oh thank god. i thought i lost it", 3),
    ("outbound", "you always think you lost it", 5),
    ("inbound", "because i usually have", 6),
    ("inbound", "are you walking tomorrow or getting dropped", 40),
    ("outbound", "walking. meet at the corner at 8", 44),
    ("inbound", "ok. bring the charger you stole", 46),
    ("outbound", "borrowed*", 47),
    ("inbound", "whatever. dont forget it again", 49),
]


async def seed_demo_traffic(store) -> None:
    """Populate one line and two conversations with plausible history."""
    line = await store.create_line("+15550000000", "Demo line", True)

    thread = await store.create_thread(line["id"], "+15550000001", "NIGHTJAR")
    base = utcnow() - timedelta(hours=20)

    last = None
    for direction, body, offset in _SCRIPT:
        created = base + timedelta(minutes=offset)
        last = await store.insert_message(
            {
                "thread_id": thread["id"],
                "line_id": line["id"],
                "direction": direction,
                "body": body,
                "media": [],
                "twilio_sid": None,
                "status": "received" if direction == "inbound" else "delivered",
                "error": None,
                "created_at": created,
            }
        )
    if last is not None:
        await store.bump_thread(
            thread["id"],
            last_message_at=last["created_at"],
            preview=last["body"],
            direction=last["direction"],
            unread_delta=1,
        )

    quiet = await store.create_thread(line["id"], "+15550000002", "KESTREL")
    created = utcnow() - timedelta(days=3)
    message = await store.insert_message(
        {
            "thread_id": quiet["id"],
            "line_id": line["id"],
            "direction": "outbound",
            "body": "ok see you monday",
            "media": [],
            "twilio_sid": None,
            "status": "delivered",
            "error": None,
            "created_at": created,
        }
    )
    await store.bump_thread(
        quiet["id"],
        last_message_at=message["created_at"],
        preview=message["body"],
        direction="outbound",
    )
