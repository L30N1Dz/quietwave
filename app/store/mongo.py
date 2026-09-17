"""MongoDB backend, on pymongo's native async client.

Document references (``line_id``, ``thread_id``) are stored as strings rather
than ObjectIds. That costs a few bytes and buys a store layer whose documents
are already JSON-shaped and whose semantics match the in-memory backend
exactly, so the test suite genuinely exercises the same code paths.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.store.base import Doc, as_utc, utcnow


def _oid(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _ser(doc: Doc | None) -> Doc | None:
    """ObjectId -> str, naive datetimes -> UTC-aware."""
    if doc is None:
        return None
    out = dict(doc)
    out["id"] = str(out.pop("_id"))
    for key, value in out.items():
        out[key] = as_utc(value)
    return out


class MongoStore:
    backend = "mongo"

    def __init__(self, uri: str, db_name: str) -> None:
        self._uri = uri
        self._db_name = db_name
        self._client: AsyncMongoClient | None = None

    # ------------------------------------------------------------------
    @property
    def _db(self):
        if self._client is None:
            raise RuntimeError("store used before startup()")
        return self._client[self._db_name]

    async def startup(self) -> None:
        self._client = AsyncMongoClient(
            self._uri, serverSelectionTimeoutMS=5000, tz_aware=True
        )
        await self._db.lines.create_index([("twilio_number", ASCENDING)], unique=True)
        await self._db.threads.create_index(
            [("line_id", ASCENDING), ("counterpart_number", ASCENDING)], unique=True
        )
        await self._db.threads.create_index([("last_message_at", DESCENDING)])
        await self._db.messages.create_index(
            [("thread_id", ASCENDING), ("created_at", ASCENDING)]
        )
        await self._db.messages.create_index([("twilio_sid", ASCENDING)], sparse=True)
        await self._db.messages.create_index(
            [("media.public_token", ASCENDING)], sparse=True
        )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def ping(self) -> bool:
        try:
            await self._db.command("ping")
            return True
        except Exception:
            return False

    # ---- lines -------------------------------------------------------
    async def create_line(
        self, twilio_number: str, label: str, active: bool = True
    ) -> Doc:
        doc = {
            "twilio_number": twilio_number,
            "label": label,
            "active": active,
            "created_at": utcnow(),
        }
        try:
            result = await self._db.lines.insert_one(doc)
        except DuplicateKeyError:
            existing = await self.get_line_by_number(twilio_number)
            if existing:
                return existing
            raise
        doc["_id"] = result.inserted_id
        return _ser(doc)

    async def list_lines(self, active_only: bool = True) -> list[Doc]:
        query = {"active": True} if active_only else {}
        cursor = self._db.lines.find(query).sort("created_at", ASCENDING)
        return [_ser(d) for d in await cursor.to_list(length=100)]

    async def get_line(self, line_id: str) -> Doc | None:
        oid = _oid(line_id)
        if oid is None:
            return None
        return _ser(await self._db.lines.find_one({"_id": oid}))

    async def get_line_by_number(self, twilio_number: str) -> Doc | None:
        return _ser(await self._db.lines.find_one({"twilio_number": twilio_number}))

    async def update_line(self, line_id: str, **fields: Any) -> Doc | None:
        oid = _oid(line_id)
        if oid is None:
            return None
        return _ser(
            await self._db.lines.find_one_and_update(
                {"_id": oid},
                {"$set": fields},
                return_document=ReturnDocument.AFTER,
            )
        )

    # ---- threads -----------------------------------------------------
    async def create_thread(
        self,
        line_id: str,
        counterpart_number: str,
        counterpart_label: str | None = None,
    ) -> Doc:
        now = utcnow()
        doc = {
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
        try:
            result = await self._db.threads.insert_one(doc)
        except DuplicateKeyError:
            existing = await self.find_thread(line_id, counterpart_number)
            if existing:
                return existing
            raise
        doc["_id"] = result.inserted_id
        return _ser(doc)

    async def find_thread(self, line_id: str, counterpart_number: str) -> Doc | None:
        return _ser(
            await self._db.threads.find_one(
                {"line_id": line_id, "counterpart_number": counterpart_number}
            )
        )

    async def get_thread(self, thread_id: str) -> Doc | None:
        oid = _oid(thread_id)
        if oid is None:
            return None
        return _ser(await self._db.threads.find_one({"_id": oid}))

    async def list_threads(self, line_ids: list[str] | None = None) -> list[Doc]:
        query = {"line_id": {"$in": line_ids}} if line_ids is not None else {}
        cursor = self._db.threads.find(query).sort("last_message_at", DESCENDING)
        return [_ser(d) for d in await cursor.to_list(length=500)]

    async def update_thread(self, thread_id: str, **fields: Any) -> Doc | None:
        oid = _oid(thread_id)
        if oid is None:
            return None
        return _ser(
            await self._db.threads.find_one_and_update(
                {"_id": oid},
                {"$set": fields},
                return_document=ReturnDocument.AFTER,
            )
        )

    async def bump_thread(
        self,
        thread_id: str,
        last_message_at: datetime,
        preview: str,
        direction: str,
        unread_delta: int = 0,
        has_media: bool = False,
    ) -> None:
        oid = _oid(thread_id)
        if oid is None:
            return
        update: Doc = {
            "$set": {
                "last_message_at": last_message_at,
                "last_message_preview": preview,
                "last_message_direction": direction,
                "last_message_has_media": has_media,
            }
        }
        if unread_delta:
            update["$inc"] = {"unread_count": unread_delta}
        await self._db.threads.update_one({"_id": oid}, update)

    async def clear_unread(self, thread_id: str) -> Doc | None:
        return await self.update_thread(thread_id, unread_count=0)

    async def delete_thread(self, thread_id: str) -> int:
        oid = _oid(thread_id)
        if oid is None:
            return 0
        await self._db.messages.delete_many({"thread_id": thread_id})
        result = await self._db.threads.delete_one({"_id": oid})
        return result.deleted_count

    # ---- messages ----------------------------------------------------
    async def insert_message(self, doc: Doc) -> Doc:
        payload = dict(doc)
        payload.setdefault("created_at", utcnow())
        result = await self._db.messages.insert_one(payload)
        payload["_id"] = result.inserted_id
        return _ser(payload)

    async def list_messages(
        self, thread_id: str, after: datetime | None = None, limit: int = 250
    ) -> list[Doc]:
        query: Doc = {"thread_id": thread_id}
        if after is not None:
            query["created_at"] = {"$gt": after}
        cursor = self._db.messages.find(query).sort("created_at", ASCENDING).limit(limit)
        return [_ser(d) for d in await cursor.to_list(length=limit)]

    async def get_message(self, message_id: str) -> Doc | None:
        oid = _oid(message_id)
        if oid is None:
            return None
        return _ser(await self._db.messages.find_one({"_id": oid}))

    async def find_message_by_sid(self, twilio_sid: str) -> Doc | None:
        return _ser(await self._db.messages.find_one({"twilio_sid": twilio_sid}))

    async def update_message(self, message_id: str, **fields: Any) -> Doc | None:
        oid = _oid(message_id)
        if oid is None:
            return None
        return _ser(
            await self._db.messages.find_one_and_update(
                {"_id": oid},
                {"$set": fields},
                return_document=ReturnDocument.AFTER,
            )
        )

    async def update_message_by_sid(self, twilio_sid: str, **fields: Any) -> Doc | None:
        return _ser(
            await self._db.messages.find_one_and_update(
                {"twilio_sid": twilio_sid},
                {"$set": fields},
                return_document=ReturnDocument.AFTER,
            )
        )

    async def patch_media(self, message_id: str, index: int, patch: Doc) -> Doc | None:
        oid = _oid(message_id)
        if oid is None:
            return None
        # arrayFilters rather than read-modify-write: sibling attachments are
        # downloaded concurrently and must not clobber each other.
        sets = {f"media.$[m].{key}": value for key, value in patch.items()}
        return _ser(
            await self._db.messages.find_one_and_update(
                {"_id": oid},
                {"$set": sets},
                array_filters=[{"m.index": index}],
                return_document=ReturnDocument.AFTER,
            )
        )

    async def find_by_media_token(self, token: str) -> tuple[Doc, int] | None:
        doc = await self._db.messages.find_one({"media.public_token": token})
        if doc is None:
            return None
        for entry in doc.get("media", []):
            if entry.get("public_token") == token:
                return _ser(doc), int(entry["index"])
        return None
