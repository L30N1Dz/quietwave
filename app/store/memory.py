"""In-process backend. Used by the test suite and by demo mode.

Mirrors MongoStore's semantics exactly, including the uniqueness rules the
Mongo indexes enforce, so a test that passes here means something. Everything
lives in dicts and dies with the process.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import Any

from app.store.base import Doc, utcnow


def _new_id() -> str:
    # 24 hex chars, so ids are indistinguishable in shape from an ObjectId.
    return uuid.uuid4().hex[:24]


class MemoryStore:
    backend = "memory"

    def __init__(self) -> None:
        self._lines: dict[str, Doc] = {}
        self._threads: dict[str, Doc] = {}
        self._messages: dict[str, Doc] = {}

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def ping(self) -> bool:
        return True

    # ---- lines -------------------------------------------------------
    async def create_line(
        self, twilio_number: str, label: str, active: bool = True
    ) -> Doc:
        existing = await self.get_line_by_number(twilio_number)
        if existing:
            return existing
        doc = {
            "id": _new_id(),
            "twilio_number": twilio_number,
            "label": label,
            "active": active,
            "created_at": utcnow(),
        }
        self._lines[doc["id"]] = doc
        return copy.deepcopy(doc)

    async def list_lines(self, active_only: bool = True) -> list[Doc]:
        rows = [
            d
            for d in self._lines.values()
            if (d.get("active") is True or not active_only)
        ]
        rows.sort(key=lambda d: d["created_at"])
        return copy.deepcopy(rows)

    async def get_line(self, line_id: str) -> Doc | None:
        return copy.deepcopy(self._lines.get(line_id))

    async def get_line_by_number(self, twilio_number: str) -> Doc | None:
        for doc in self._lines.values():
            if doc["twilio_number"] == twilio_number:
                return copy.deepcopy(doc)
        return None

    async def update_line(self, line_id: str, **fields: Any) -> Doc | None:
        doc = self._lines.get(line_id)
        if doc is None:
            return None
        doc.update(fields)
        return copy.deepcopy(doc)

    # ---- threads -----------------------------------------------------
    async def create_thread(
        self,
        line_id: str,
        counterpart_number: str,
        counterpart_label: str | None = None,
    ) -> Doc:
        existing = await self.find_thread(line_id, counterpart_number)
        if existing:
            return existing
        now = utcnow()
        doc = {
            "id": _new_id(),
            "line_id": line_id,
            "counterpart_number": counterpart_number,
            "counterpart_label": counterpart_label,
            "last_message_at": now,
            "last_message_preview": "",
            "last_message_direction": None,
            "last_message_has_media": False,
            "unread_count": 0,
            "created_at": now,
        }
        self._threads[doc["id"]] = doc
        return copy.deepcopy(doc)

    async def find_thread(self, line_id: str, counterpart_number: str) -> Doc | None:
        for doc in self._threads.values():
            if (
                doc["line_id"] == line_id
                and doc["counterpart_number"] == counterpart_number
            ):
                return copy.deepcopy(doc)
        return None

    async def get_thread(self, thread_id: str) -> Doc | None:
        return copy.deepcopy(self._threads.get(thread_id))

    async def list_threads(self, line_ids: list[str] | None = None) -> list[Doc]:
        rows = [
            d
            for d in self._threads.values()
            if line_ids is None or d["line_id"] in line_ids
        ]
        rows.sort(key=lambda d: d["last_message_at"], reverse=True)
        return copy.deepcopy(rows)

    async def update_thread(self, thread_id: str, **fields: Any) -> Doc | None:
        doc = self._threads.get(thread_id)
        if doc is None:
            return None
        doc.update(fields)
        return copy.deepcopy(doc)

    async def bump_thread(
        self,
        thread_id: str,
        last_message_at: datetime,
        preview: str,
        direction: str,
        unread_delta: int = 0,
        has_media: bool = False,
    ) -> None:
        doc = self._threads.get(thread_id)
        if doc is None:
            return
        doc["last_message_at"] = last_message_at
        doc["last_message_preview"] = preview
        doc["last_message_direction"] = direction
        doc["last_message_has_media"] = has_media
        if unread_delta:
            doc["unread_count"] = max(0, doc.get("unread_count", 0) + unread_delta)

    async def clear_unread(self, thread_id: str) -> Doc | None:
        return await self.update_thread(thread_id, unread_count=0)

    async def delete_thread(self, thread_id: str) -> int:
        if thread_id not in self._threads:
            return 0
        for mid in [
            m["id"] for m in self._messages.values() if m["thread_id"] == thread_id
        ]:
            del self._messages[mid]
        del self._threads[thread_id]
        return 1

    # ---- messages ----------------------------------------------------
    async def insert_message(self, doc: Doc) -> Doc:
        payload = copy.deepcopy(doc)
        payload["id"] = _new_id()
        payload.setdefault("created_at", utcnow())
        self._messages[payload["id"]] = payload
        return copy.deepcopy(payload)

    async def list_messages(
        self, thread_id: str, after: datetime | None = None, limit: int = 250
    ) -> list[Doc]:
        rows = [
            d
            for d in self._messages.values()
            if d["thread_id"] == thread_id
            and (after is None or d["created_at"] > after)
        ]
        rows.sort(key=lambda d: d["created_at"])
        return copy.deepcopy(rows[:limit])

    async def get_message(self, message_id: str) -> Doc | None:
        return copy.deepcopy(self._messages.get(message_id))

    async def find_message_by_sid(self, twilio_sid: str) -> Doc | None:
        for doc in self._messages.values():
            if doc.get("twilio_sid") == twilio_sid:
                return copy.deepcopy(doc)
        return None

    async def update_message(self, message_id: str, **fields: Any) -> Doc | None:
        doc = self._messages.get(message_id)
        if doc is None:
            return None
        doc.update(fields)
        return copy.deepcopy(doc)

    async def update_message_by_sid(self, twilio_sid: str, **fields: Any) -> Doc | None:
        for doc in self._messages.values():
            if doc.get("twilio_sid") == twilio_sid:
                doc.update(fields)
                return copy.deepcopy(doc)
        return None

    async def patch_media(self, message_id: str, index: int, patch: Doc) -> Doc | None:
        doc = self._messages.get(message_id)
        if doc is None:
            return None
        for entry in doc.get("media", []):
            if int(entry.get("index", -1)) == index:
                entry.update(patch)
        return copy.deepcopy(doc)

    async def find_by_media_token(self, token: str) -> tuple[Doc, int] | None:
        for doc in self._messages.values():
            for entry in doc.get("media", []):
                if entry.get("public_token") == token:
                    return copy.deepcopy(doc), int(entry["index"])
        return None
