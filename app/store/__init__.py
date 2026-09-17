"""Persistence layer.

Two interchangeable backends behind one interface:

* ``MongoStore``  -- the real one, MongoDB.
* ``MemoryStore`` -- in-process dicts. Used by the test suite and by
  ``--demo`` so the interface can be exercised without a database.

Everything crossing this boundary is a plain ``dict`` with string ids, so
route code never sees an ObjectId.
"""

from __future__ import annotations

from app.config import Settings
from app.store.base import Store
from app.store.memory import MemoryStore
from app.store.mongo import MongoStore

__all__ = ["Store", "MemoryStore", "MongoStore", "build_store"]


def build_store(settings: Settings) -> Store:
    if settings.store_backend == "memory":
        return MemoryStore()
    return MongoStore(settings.mongo_uri, settings.mongo_db_name)
