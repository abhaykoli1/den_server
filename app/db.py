"""Database layer.

Prod  -> Motor (async MongoDB Atlas)
Dev   -> mongomock in-memory (MONGODB_URI=mongomock://demo) with JSON snapshot
         persistence: dumps all collections every 5s + at exit, re-hydrates on
         boot before index creation. Logins and data survive restarts;
         delete backend/.devdata/snapshot.json for a fresh start.

Every business entity uses a string `id` field (never `_id`), so documents are
plain JSON and snapshots are trivially serializable.
"""
import atexit
import json
import os
import threading
from copy import deepcopy

from .config import settings

COLLECTIONS = [
    "users", "subscription_plans", "clubs", "tables", "members", "plans",
    "sessions", "frames", "menu_items", "item_bills", "membership_sales",
    "expenses", "tournaments", "mailouts", "platform_config", "logs",
]

_db = None
_snapshot_thread_started = False


def _clean(doc):
    if doc is None:
        return None
    d = dict(doc)
    d.pop("_id", None)
    return d


# ---------------------------------------------------------------- mongomock
class _MockResult:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        docs = self._docs if length is None else self._docs[:length]
        return [deepcopy(d) for d in docs]


class MockCollection:
    """Async facade with the exact surface our routers use."""

    def __init__(self, coll):
        self._c = coll

    async def find_one(self, filter=None, **kw):
        return _clean(self._c.find_one(filter or {}, **kw))

    def find(self, filter=None, **kw):
        return _MockResult([_clean(d) for d in self._c.find(filter or {}, **kw)])

    async def insert_one(self, doc):
        self._c.insert_one(dict(doc))

    async def update_one(self, filter, update, upsert=False):
        return self._c.update_one(filter, update, upsert=upsert)

    async def update_many(self, filter, update):
        return self._c.update_many(filter, update)

    async def delete_one(self, filter):
        return self._c.delete_one(filter)

    async def delete_many(self, filter):
        return self._c.delete_many(filter)

    async def count_documents(self, filter):
        return self._c.count_documents(filter)

    async def find_one_and_update(self, filter, update, after=True, upsert=False):
        from pymongo import ReturnDocument
        r = self._c.find_one_and_update(
            filter, update, upsert=upsert,
            return_document=ReturnDocument.AFTER if after else ReturnDocument.BEFORE,
        )
        return _clean(r)

    async def create_index(self, keys, **kw):
        return self._c.create_index(keys, **kw)


class MockDB:
    def __init__(self, raw):
        self._raw = raw

    def __getattr__(self, name):
        return MockCollection(getattr(self._raw, name))

    def raw_collection(self, name):
        return getattr(self._raw, name)


# ------------------------------------------------------------------- Motor
class _MotorCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    async def to_list(self, length=None):
        return [_clean(d) for d in await self._cursor.to_list(length)]


class MotorCollection:
    def __init__(self, coll):
        self._c = coll

    async def find_one(self, filter=None, **kw):
        return _clean(await self._c.find_one(filter or {}, **kw))

    def find(self, filter=None, **kw):
        return _MotorCursor(self._c.find(filter or {}, **kw))

    async def insert_one(self, doc):
        await self._c.insert_one(dict(doc))

    async def update_one(self, filter, update, upsert=False):
        return await self._c.update_one(filter, update, upsert=upsert)

    async def update_many(self, filter, update):
        return await self._c.update_many(filter, update)

    async def delete_one(self, filter):
        return await self._c.delete_one(filter)

    async def delete_many(self, filter):
        return await self._c.delete_many(filter)

    async def count_documents(self, filter):
        return await self._c.count_documents(filter)

    async def find_one_and_update(self, filter, update, after=True, upsert=False):
        from pymongo import ReturnDocument
        r = await self._c.find_one_and_update(
            filter, update, upsert=upsert,
            return_document=ReturnDocument.AFTER if after else ReturnDocument.BEFORE,
        )
        return _clean(r)

    async def create_index(self, keys, **kw):
        return await self._c.create_index(keys, **kw)


class MotorDB:
    def __init__(self, raw):
        self._raw = raw

    def __getattr__(self, name):
        return MotorCollection(getattr(self._raw, name))


# ------------------------------------------------------------------ snapshot
def _snapshot_path() -> str:
    return os.path.join(settings.DATA_DIR, "snapshot.json")


def _snapshot_off() -> bool:
    return os.environ.get("SNAPSHOT_DISABLED", "").strip().lower() in ("1", "true", "yes")


def dump_snapshot():
    """Atomic tmp+rename dump of every collection (dev only)."""
    if not settings.is_mock_db or _db is None or _snapshot_off():
        return
    try:
        os.makedirs(settings.DATA_DIR, exist_ok=True)
        data = {}
        for name in COLLECTIONS:
            data[name] = [_clean(d) for d in _db.raw_collection(name).find({})]
        tmp = _snapshot_path() + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, _snapshot_path())
    except Exception as exc:  # never kill the app over persistence
        print(f"[snapshot] dump failed: {exc}")


def _hydrate():
    if _snapshot_off():
        return
    path = _snapshot_path()
    if not os.path.exists(path):
        return
    try:
        with open(path) as fh:
            data = json.load(fh)
        for name, docs in data.items():
            if docs:
                _db.raw_collection(name).insert_many([dict(d) for d in docs])
        print(f"[snapshot] re-hydrated {sum(len(v) for v in data.values())} docs")
    except Exception as exc:
        print(f"[snapshot] hydrate failed: {exc}")


def _snapshot_loop():
    global _snapshot_thread_started
    if _snapshot_thread_started or _snapshot_off():
        return
    _snapshot_thread_started = True

    def tick():
        dump_snapshot()
        timer = threading.Timer(5.0, tick)
        timer.daemon = True
        timer.start()

    timer = threading.Timer(5.0, tick)
    timer.daemon = True
    timer.start()
    atexit.register(dump_snapshot)


# ---------------------------------------------------------------------- boot
async def ensure_indexes(db):
    """Per-index, fault tolerant (warn + continue)."""
    owned = ["tables", "members", "plans", "sessions", "frames", "menu_items",
             "item_bills", "membership_sales", "expenses", "tournaments", "logs"]
    plans = []
    for name in owned:
        plans.append((name, [("clubId", 1)]))
        plans.append((name, [("clubId", 1), ("id", 1)]))
    plans += [
        ("frames", [("clubId", 1), ("createdAt", -1)]),
        ("expenses", [("clubId", 1), ("date", 1)]),
        ("logs", [("clubId", 1), ("createdAt", -1)]),
        ("membership_sales", [("clubId", 1), ("createdAt", -1)]),
        ("mailouts", [("createdAt", -1)]),
    ]
    for coll, keys in plans:
        try:
            await getattr(db, coll).create_index(keys)
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code in (85, 86):  # IndexOptions/KeySpecsConflict — Atlas pe pehle se same-key index hai
                print(f"[indexes] {coll}: matching index already exists — skipped")
            else:
                print(f"[indexes] {coll} {keys}: {exc}")


async def get_db():
    """FastAPI dependency + module entrypoint. Boots lazily."""
    global _db
    if _db is not None:
        return _db
    if settings.is_mock_db:
        import mongomock
        client = mongomock.MongoClient()
        _db = MockDB(client[settings.MONGODB_DB])
        _hydrate()
        _snapshot_loop()
    else:
        from motor.motor_asyncio import AsyncIOMotorClient
        uri = settings.MONGODB_URI
        if not uri.startswith(("mongodb://", "mongodb+srv://")):
            raise RuntimeError(
                "MONGODB_URI empty/malformed — expected 'mongodb+srv://user:pass@cluster.mongodb.net/db?…' "
                "as a single line (no quotes, no spaces, no trailing comma). Fix the env var and REDEPLOY.")
        client = AsyncIOMotorClient(uri)
        _db = MotorDB(client[settings.MONGODB_DB])
    await ensure_indexes(_db)
    return _db

# dsds
async def reset_db_for_tests():
    global _db, _snapshot_thread_started
    _db = None
    _snapshot_thread_started = False
