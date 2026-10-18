import asyncio
import datetime
import logging
import re
import time

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
from pyrogram import filters

from config import DATABASE_URI, DATABASE_NAME, OWNERS, MESSAGE_MAP_DAYS

logger = logging.getLogger(__name__)

client = AsyncIOMotorClient(DATABASE_URI)
db = client[DATABASE_NAME]

users_col = db.users
admins_col = db.admins
banned_col = db.banned
auto_replies_col = db.auto_replies
force_channels_col = db.force_channels
join_requests_col = db.join_requests
content_col = db.content_index
indexed_channels_col = db.indexed_channels
msg_map_col = db.msg_map
msg_links_col = db.msg_links


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


async def ensure_indexes():
    """Creates the Mongo indexes the bot relies on. Safe to call on every start;
    a failure on one index is logged and never stops the bot."""
    specs = [
        (users_col, [("user_id", 1)], {}),
        (banned_col, [("user_id", 1)], {}),
        (msg_map_col, [("admin_id", 1), ("msg_id", 1)], {"unique": True}),
        (msg_map_col, [("at", 1)], {"expireAfterSeconds": MESSAGE_MAP_DAYS * 86400}),
        (join_requests_col, [("user_id", 1), ("chat_id", 1)], {"unique": True}),
        (msg_links_col, [("chat_id", 1), ("msg_id", 1), ("peer_chat", 1)], {"unique": True}),
        (msg_links_col, [("at", 1)], {"expireAfterSeconds": MESSAGE_MAP_DAYS * 86400}),
    ]
    for col, keys, kwargs in specs:
        try:
            await col.create_index(keys, **kwargs)
        except Exception as e:
            logger.warning(f"index {col.name}{keys} not created: {type(e).__name__}: {e}")


# ---------------- Forwarded-message → user map ----------------
# Stored in Mongo (not RAM) so replies keep working after a restart/redeploy and
# for users who hide their identity on forwards.

async def save_forward_map(admin_id: int, msg_id: int, user_id: int):
    await msg_map_col.update_one(
        {"admin_id": admin_id, "msg_id": msg_id},
        {"$set": {"user_id": user_id, "at": _now()}},
        upsert=True,
    )

async def get_forward_user(admin_id: int, msg_id: int):
    doc = await msg_map_col.find_one({"admin_id": admin_id, "msg_id": msg_id})
    return doc["user_id"] if doc else None


# ---------------- Message links (edit + reaction sync) ----------------
# Every message the bot relays is linked to its copy on the other side, in BOTH directions:
#   kind "user":  the user wrote it  -> (user chat, msg) <-> (admin chat, forwarded msg)
#   kind "admin": an admin wrote it  -> (admin chat, msg) <-> (user chat, copied msg)
# so an edit or a reaction on either message can be passed to the other one.

async def save_link(chat_a: int, msg_a: int, chat_b: int, msg_b: int, kind: str):
    now = _now()
    await msg_links_col.bulk_write([
        UpdateOne({"chat_id": chat_a, "msg_id": msg_a, "peer_chat": chat_b},
                  {"$set": {"peer_msg": msg_b, "kind": kind, "at": now}}, upsert=True),
        UpdateOne({"chat_id": chat_b, "msg_id": msg_b, "peer_chat": chat_a},
                  {"$set": {"peer_msg": msg_a, "kind": kind, "at": now}}, upsert=True),
    ], ordered=False)

async def get_links(chat_id: int, msg_id: int) -> list:
    """[{peer_chat, peer_msg, kind}] for this message (empty when it was never relayed)."""
    return [d async for d in msg_links_col.find({"chat_id": chat_id, "msg_id": msg_id})]


# ---------------- Users ----------------

_user_seen = {}  # user_id -> last time we wrote this user, so we don't hit Mongo on every message
_USER_WRITE_EVERY = 600  # seconds

async def add_user(user_id: int, first_name: str = "", username: str = ""):
    now = time.time()
    if now - _user_seen.get(user_id, 0) < _USER_WRITE_EVERY:
        return
    await users_col.update_one(
        {"user_id": user_id},
        {
            "$set": {"user_id": user_id, "first_name": first_name, "username": username},
            "$setOnInsert": {"joined_at": _now()},
        },
        upsert=True,
    )
    _user_seen[user_id] = now
    if len(_user_seen) > 50000:
        _user_seen.clear()

async def get_user(user_id: int):
    return await users_col.find_one({"user_id": user_id})

async def is_user_exist(user_id: int) -> bool:
    return await users_col.find_one({"user_id": user_id}) is not None

async def delete_user(user_id: int):
    """Removes a user from the user list (used when they blocked the bot / deleted their account)."""
    _user_seen.pop(user_id, None)
    await users_col.delete_many({"user_id": user_id})

async def get_all_users():
    return users_col.find({})

async def total_users_count() -> int:
    return await users_col.count_documents({})

async def get_broadcast_targets() -> list:
    """[{user_id, first_name}] for everyone who can receive a broadcast (banned users excluded)."""
    banned = await _ban_set()
    targets = []
    async for doc in users_col.find({}, {"user_id": 1, "first_name": 1}):
        uid = doc.get("user_id")
        if uid is None or uid in banned:
            continue
        targets.append({"user_id": uid, "first_name": doc.get("first_name") or ""})
    return targets


# ---------------- Banned (kept in memory: ban checks run on every update) ----------------

_banned_ids = None
_ban_lock = None

async def _ban_set() -> set:
    global _banned_ids, _ban_lock
    if _banned_ids is not None:
        return _banned_ids
    if _ban_lock is None:
        _ban_lock = asyncio.Lock()
    async with _ban_lock:
        if _banned_ids is None:
            ids = set()
            async for doc in banned_col.find({}, {"user_id": 1}):
                if "user_id" in doc:
                    ids.add(int(doc["user_id"]))
            _banned_ids = ids
            logger.info(f"ban list loaded: {len(ids)} ids")
    return _banned_ids

async def ban_users(user_ids) -> int:
    """Bans many ids at once. Returns how many were NEW."""
    banned = await _ban_set()
    ids = {int(u) for u in user_ids}
    new_ids = ids - banned
    if ids:
        await banned_col.bulk_write(
            [UpdateOne({"user_id": u}, {"$set": {"user_id": u}}, upsert=True) for u in ids],
            ordered=False,
        )
        banned |= ids
    return len(new_ids)

async def unban_users(user_ids) -> int:
    """Unbans many ids. Returns how many were actually banned before."""
    banned = await _ban_set()
    ids = {int(u) for u in user_ids}
    was_banned = ids & banned
    if ids:
        await banned_col.delete_many({"user_id": {"$in": list(ids)}})
        banned -= ids
    return len(was_banned)

async def ban_user(user_id: int):
    await ban_users([user_id])

async def unban_user(user_id: int):
    await unban_users([user_id])

async def is_banned(user_id: int) -> bool:
    return int(user_id) in await _ban_set()

async def get_banned_users():
    return sorted(await _ban_set())

async def banned_users_count() -> int:
    return len(await _ban_set())


# ---------------- Admins & permissions ----------------

PERMISSIONS = {
    "can_settings": "⚙️ Settings panel",
    "can_broadcast": "📢 Broadcast",
    "can_manage_admins": "👥 Manage admins",
}
# Admins that were added before permissions existed have no flags stored, so
# these defaults apply to them.
PERM_DEFAULTS = {"can_settings": True, "can_broadcast": True, "can_manage_admins": False}

OWNER_IDS = set(OWNERS)

def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS

async def add_admin(user_id: int):
    await admins_col.update_one(
        {"user_id": user_id},
        {"$setOnInsert": {"user_id": user_id, **PERM_DEFAULTS}},
        upsert=True,
    )

async def remove_admin(user_id: int):
    await admins_col.delete_one({"user_id": user_id})

async def get_admins():
    cursor = admins_col.find({})
    return [doc["user_id"] async for doc in cursor]

async def get_admin_doc(user_id: int):
    return await admins_col.find_one({"user_id": user_id})

async def admins_count() -> int:
    others = await admins_col.count_documents({"user_id": {"$nin": list(OWNER_IDS)}})
    return len(OWNER_IDS) + others

async def is_admin(user_id: int) -> bool:
    if is_owner(user_id):
        return True
    return await admins_col.find_one({"user_id": user_id}) is not None

async def has_permission(user_id: int, perm: str) -> bool:
    if is_owner(user_id):
        return True
    doc = await admins_col.find_one({"user_id": user_id})
    if not doc:
        return False
    return bool(doc.get(perm, PERM_DEFAULTS.get(perm, False)))

async def get_permissions(user_id: int) -> dict:
    doc = await admins_col.find_one({"user_id": user_id}) or {}
    return {p: bool(doc.get(p, PERM_DEFAULTS[p])) for p in PERMISSIONS}

async def set_permission(user_id: int, perm: str, value: bool):
    if perm not in PERMISSIONS:
        raise ValueError(f"unknown permission {perm}")
    await admins_col.update_one({"user_id": user_id}, {"$set": {perm: bool(value)}})


async def _admin_filter_func(_, __, update):
    user = getattr(update, "from_user", None)
    if not user:
        return False
    return await is_admin(user.id)

async def _perm_filter_func(flt, __, update):
    user = getattr(update, "from_user", None)
    if not user:
        return False
    return await has_permission(user.id, flt.permission)

async def _owner_filter_func(_, __, update):
    user = getattr(update, "from_user", None)
    return bool(user) and is_owner(user.id)

# Any admin (owner or added admin). Works for messages and callback queries.
admin_filter = filters.create(_admin_filter_func)
# Owner(s) only.
owner_filter = filters.create(_owner_filter_func)

def admin_perm(permission: str):
    """Filter that passes owners always, and added admins only if they hold `permission`."""
    return filters.create(_perm_filter_func, permission=permission)


# ---------------- Automatic Replies ----------------

async def add_auto_reply(keyword: str, reply_text: str):
    await auto_replies_col.update_one(
        {"keyword": keyword.lower()},
        {"$set": {"keyword": keyword.lower(), "reply": reply_text}},
        upsert=True
    )

async def remove_auto_reply(keyword: str):
    await auto_replies_col.delete_one({"keyword": keyword.lower()})

async def get_auto_replies():
    cursor = auto_replies_col.find({})
    return [doc async for doc in cursor]

# "Word" characters for keyword boundaries: letters/digits/_ plus Indic scripts (Devanagari etc.),
# including their vowel signs, which Python's \w does not count. Danda marks (। ॥) stay punctuation.
_WORD = r"[\wऀ-ॣ०-෿]"

def keyword_in_text(keyword: str, text_lower: str) -> bool:
    """Whole-word / whole-phrase match: keyword 'hi' matches 'hi bro' but not 'this'."""
    keyword = (keyword or "").strip().lower()
    if not keyword:
        return False
    return re.search(rf"(?<!{_WORD}){re.escape(keyword)}(?!{_WORD})", text_lower) is not None

async def find_auto_reply(text: str):
    if not text:
        return None
    text_lower = text.lower()
    docs = [doc async for doc in auto_replies_col.find({})]
    docs.sort(key=lambda d: len(d.get("keyword", "")), reverse=True)  # most specific keyword wins
    for doc in docs:
        if keyword_in_text(doc.get("keyword", ""), text_lower):
            return doc["reply"]
    return None


# ---------------- Force Join Channels ----------------
# Each entry: {chat: <what the admin typed>, chat_id: <numeric id>, mode: "normal"|"request", link: <invite link>}
# Old entries that only have {chat} keep working (mode defaults to "normal").

async def add_force_channel(chat: str, mode: str = "normal", link: str = None, chat_id: int = None):
    await force_channels_col.update_one(
        {"chat": chat},
        {"$set": {"chat": chat, "mode": mode, "link": link, "chat_id": chat_id}},
        upsert=True
    )

async def remove_force_channel(chat: str):
    doc = await force_channels_col.find_one({"chat": chat})
    await force_channels_col.delete_one({"chat": chat})
    if doc and doc.get("chat_id") is not None:
        await join_requests_col.delete_many({"chat_id": doc["chat_id"]})

async def get_force_channels():
    cursor = force_channels_col.find({})
    return [doc["chat"] async for doc in cursor]

async def get_force_channel_docs() -> list:
    out = []
    async for doc in force_channels_col.find({}):
        out.append({
            "chat": doc["chat"],
            "chat_id": doc.get("chat_id"),
            "mode": doc.get("mode") or "normal",
            "link": doc.get("link"),
        })
    return out

async def set_force_channel_link(chat: str, link: str):
    await force_channels_col.update_one({"chat": chat}, {"$set": {"link": link}})

async def record_join_request(user_id: int, chat_id: int):
    await join_requests_col.update_one(
        {"user_id": user_id, "chat_id": chat_id},
        {"$set": {"user_id": user_id, "chat_id": chat_id, "at": _now()}},
        upsert=True
    )

async def get_join_request(user_id: int, chat_id: int):
    return await join_requests_col.find_one({"user_id": user_id, "chat_id": chat_id})

async def clear_join_request(user_id: int, chat_id: int):
    await join_requests_col.delete_many({"user_id": user_id, "chat_id": chat_id})


# ---------------- Content Index (scanned channel content, searchable) ----------------

_content_index_ready = False

async def ensure_content_text_index():
    """Creates the Mongo text index once (safe to call repeatedly — no-ops after the first time)."""
    global _content_index_ready
    if _content_index_ready:
        return
    await content_col.create_index([("text", "text")])
    _content_index_ready = True

async def add_indexed_channel(chat_id: int):
    await indexed_channels_col.update_one({"chat_id": chat_id}, {"$set": {"chat_id": chat_id}}, upsert=True)

async def get_indexed_channels() -> list:
    cursor = indexed_channels_col.find({})
    return [doc["chat_id"] async for doc in cursor]

async def is_indexed_channel(chat_id: int) -> bool:
    return await indexed_channels_col.find_one({"chat_id": chat_id}) is not None

async def add_content_item(chat_id: int, message_id: int, text: str, has_media: bool = False):
    """Stores one scanned post (text/link/image caption) so it can be searched and re-sent later."""
    if not text:
        return
    await ensure_content_text_index()
    await content_col.update_one(
        {"chat_id": chat_id, "message_id": message_id},
        {"$set": {"chat_id": chat_id, "message_id": message_id, "text": text, "has_media": has_media}},
        upsert=True
    )

async def content_count() -> int:
    return await content_col.count_documents({})

async def clear_content_index():
    """Wipes all scanned content (registered channels stay registered, so auto-index of new posts keeps working)."""
    await content_col.delete_many({})

async def search_content(query: str, limit: int = 1, min_score: float = 0.0) -> list:
    """Full-text search over indexed content. Returns the best-matching doc(s), best first.
    Docs whose text score is below `min_score` are dropped (stops random posts being sent for 'ok', 'thanks', ...)."""
    if not query:
        return []
    await ensure_content_text_index()
    cursor = content_col.find(
        {"$text": {"$search": query}},
        {"score": {"$meta": "textScore"}, "chat_id": 1, "message_id": 1, "text": 1, "has_media": 1}
    ).sort([("score", {"$meta": "textScore"})]).limit(limit)
    return [doc async for doc in cursor if doc.get("score", 0) >= min_score]
