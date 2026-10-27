"""Force-join (force subscribe) for the contact bot.

Two modes per channel (chosen when the admin adds it in /settings → Force join):

* normal  - the user must actually be a member (checked with get_chat_member).
* request - the channel uses "approve new members"; the user only has to SEND a
            join request. We record it when Telegram tells us about it (the bot
            must be an admin of that channel), so no manual approval is needed.

Membership checks are cached for a few minutes, so a user chatting with the bot
does not trigger a get_chat_member call for every single message.
"""
import asyncio
import datetime
import html
import logging
import re
import time
import types

from pyrogram import Client, filters, enums
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from logchannel import log_event
from database import (
    get_force_channel_docs, set_force_channel_link, record_join_request,
    get_join_request, clear_join_request, is_admin,
)

logger = logging.getLogger(__name__)

JOINED_CACHE_SECONDS = 300      # a positive check is trusted for 5 minutes
JOIN_REQUEST_TTL_DAYS = 3       # after this a saved join request is re-verified in the background

_joined_cache = {}   # (user_id, target) -> expiry timestamp
_link_cache = {}     # target -> (title, link)
_sem = None


def _semaphore():
    """Limits parallel get_chat_member calls (avoids FloodWait bursts). Created lazily so it binds to the running loop."""
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(8)
    return _sem


def _target(entry):
    """What to pass to the Telegram API for this entry: numeric id when known, else the @username."""
    if entry.get("chat_id") is not None:
        return entry["chat_id"]
    chat = str(entry["chat"]).strip()
    return int(chat) if re.fullmatch(r"-?\d+", chat) else chat


def _numeric_id(entry):
    target = _target(entry)
    return target if isinstance(target, int) else None


def _cache_hit(key) -> bool:
    return _joined_cache.get(key, 0) > time.time()


def _cache_set(key):
    if len(_joined_cache) > 20000:
        now = time.time()
        for k in [k for k, v in _joined_cache.items() if v <= now]:
            _joined_cache.pop(k, None)
    _joined_cache[key] = time.time() + JOINED_CACHE_SECONDS


def clear_user_cache(user_id: int):
    for key in [k for k in _joined_cache if k[0] == user_id]:
        _joined_cache.pop(key, None)


async def _is_member(client: Client, target, user_id: int):
    """True / False, or None when the check itself failed (bot not admin there, bad chat...)."""
    try:
        async with _semaphore():
            member = await client.get_chat_member(target, user_id)
    except UserNotParticipant:
        return False
    except Exception as e:
        logger.warning(f"[FSUB] get_chat_member({target}, {user_id}) failed: {type(e).__name__}: {e}")
        return None
    if member.status in (enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED):
        return False
    if member.status == enums.ChatMemberStatus.RESTRICTED and getattr(member, "is_member", True) is False:
        return False
    return True


async def _reverify_stale(client: Client, user_id: int, chat_id: int):
    """Background check of an old join-request record (a plain 'decline' sends the bot no event)."""
    status = await _is_member(client, chat_id, user_id)
    if status is False:
        await clear_join_request(user_id, chat_id)
        logger.info(f"[FSUB] stale join request cleared: user={user_id} chat={chat_id}")
    elif status is True:
        await record_join_request(user_id, chat_id)


async def _check_entry(client: Client, entry: dict, user_id: int):
    """True (joined) / False (not joined) / None (could not check - never lock users out because of a misconfigured channel)."""
    target = _target(entry)
    key = (user_id, str(target))
    if _cache_hit(key):
        return True

    chat_id = _numeric_id(entry)
    if entry["mode"] == "request" and chat_id is not None:
        rec = await get_join_request(user_id, chat_id)
        if rec:
            at = rec.get("at")
            if at is not None:
                if at.tzinfo is None:
                    at = at.replace(tzinfo=datetime.timezone.utc)
                age_days = (datetime.datetime.now(datetime.timezone.utc) - at).days
                if age_days >= JOIN_REQUEST_TTL_DAYS:
                    asyncio.create_task(_reverify_stale(client, user_id, chat_id))
            _cache_set(key)
            return True

    status = await _is_member(client, target, user_id)
    if status is True:
        if entry["mode"] == "request" and chat_id is not None:
            try:
                await record_join_request(user_id, chat_id)
            except Exception:
                pass
        _cache_set(key)
    return status


async def get_not_joined_entries(client: Client, user_id: int) -> list:
    """Force-join entries (dicts) the user has not satisfied yet. Owners and admins are never forced to join."""
    if await is_admin(user_id):
        return []
    entries = await get_force_channel_docs()
    if not entries:
        return []
    results = await asyncio.gather(*[_check_entry(client, e, user_id) for e in entries])
    return [e for e, ok in zip(entries, results) if ok is False]


async def get_not_joined_channels(client: Client, user_id: int) -> list:
    """Same as get_not_joined_entries but returns just the chat strings (kept for compatibility)."""
    return [e["chat"] for e in await get_not_joined_entries(client, user_id)]


async def is_subscribed(client: Client, user_id: int) -> bool:
    return len(await get_not_joined_entries(client, user_id)) == 0


async def _join_info(client: Client, entry: dict):
    """(title, link) for the Join button of this entry."""
    target = _target(entry)
    mode = entry["mode"]

    if mode == "request":
        link = entry.get("link")
        title = None
        if not link:
            try:
                invite = await client.create_chat_invite_link(target, creates_join_request=True, name="Contact bot force join")
                link = invite.invite_link
                await set_force_channel_link(entry["chat"], link)
            except Exception as e:
                logger.warning(f"[FSUB] couldn't create join-request link for {target}: {type(e).__name__}: {e}")
        return title, link

    cached = _link_cache.get(str(target))
    if cached and cached[1]:
        return cached

    title = link = None
    try:
        chat = await client.get_chat(target)
        title = chat.title
        if chat.username:
            link = f"https://t.me/{chat.username}"
        elif chat.invite_link:
            link = chat.invite_link
        else:
            try:
                link = await client.export_chat_invite_link(target)
            except Exception:
                link = None
    except Exception as e:
        logger.warning(f"[FSUB] couldn't read chat {target}: {type(e).__name__}: {e}")

    if not link and isinstance(target, str):
        link = f"https://t.me/{target.lstrip('@')}"
    _link_cache[str(target)] = (title, link)
    return title, link


async def force_sub_markup(client: Client, user_id: int) -> InlineKeyboardMarkup:
    missing = await get_not_joined_entries(client, user_id)
    infos = await asyncio.gather(*[_join_info(client, e) for e in missing])

    join_buttons = []
    for index, (entry, (title, link)) in enumerate(zip(missing, infos), start=1):
        if not link:
            continue
        label = f"📢 Join {title}" if title else f"📢 Join channel {index}"
        if entry["mode"] == "request":
            label = f"📨 Request to join {index}" if len(missing) > 1 else "📨 Request to join"
        join_buttons.append(InlineKeyboardButton(label, url=link))

    rows = [join_buttons[i:i + 2] for i in range(0, len(join_buttons), 2)]
    rows.append([InlineKeyboardButton("🔄 I've Joined", callback_data="check_fsub")])
    return InlineKeyboardMarkup(rows)


FORCE_SUB_TEXT = "<blockquote><b>Please Join Our Channel(s) First To Use This Bot.</b></blockquote>"


async def send_force_sub_prompt(client: Client, message):
    await message.reply(
        FORCE_SUB_TEXT,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=await force_sub_markup(client, message.from_user.id),
    )


# ---------------- Handlers ----------------

@Client.on_callback_query(filters.regex("^check_fsub$"))
async def check_fsub_cb(client: Client, query):
    user_id = query.from_user.id
    clear_user_cache(user_id)   # force a fresh look, the user just claimed they joined
    missing = await get_not_joined_entries(client, user_id)
    if not missing:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.answer("✅ Thanks for joining! You can use the bot now.", show_alert=True)
    else:
        await query.answer(f"❌ You haven't joined all channels yet ({len(missing)} left).", show_alert=True)


@Client.on_chat_join_request()
async def track_join_request(client: Client, update):
    """A user sent a join request to a channel. For 'request' mode channels that is all we need."""
    entries = await get_force_channel_docs()
    if not any(e["mode"] == "request" and _numeric_id(e) == update.chat.id for e in entries):
        return
    try:
        await record_join_request(update.from_user.id, update.chat.id)
    except Exception as e:
        logger.error(f"[FSUB] couldn't save join request: {type(e).__name__}: {e}")
        return
    clear_user_cache(update.from_user.id)
    try:
        await client.send_message(update.from_user.id, "<b>✅ Request Received! You Can Use The Bot Now.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass   # user may never have started the bot - nothing to do


@Client.on_raw_update(group=3)
async def bot_stopped_update(client: Client, update, users, chats):
    """A user blocked / unblocked the bot. Telegram tells the bot at once ("UpdateBotStopped"); this reads that
    raw update directly, so it works whatever the library does with it."""
    if type(update).__name__ != "UpdateBotStopped":
        return
    uid = update.user_id
    stopped = bool(update.stopped)
    logger.info(f"[BOT STOPPED] user {uid} {'blocked' if stopped else 'unblocked'} the bot")
    clear_user_cache(uid)
    raw = (users or {}).get(uid)
    user = None
    if raw is not None and getattr(raw, "first_name", None) is not None:
        user = types.SimpleNamespace(id=uid, first_name=raw.first_name or "", username=getattr(raw, "username", None) or "")
    log_event(client, "🚫 User Blocked The Bot" if stopped else "✅ User Unblocked The Bot",
              user=user, uid=None if user else uid)


@Client.on_chat_member_updated()
async def handle_member_left(client: Client, update):
    """Logs blocks / unblocks of the bot and force-join channel joins / leaves. When someone leaves / is removed
    from a 'request' channel, forget their request so they must send a new one."""
    new = update.new_chat_member
    old = update.old_chat_member
    user = (new.user if new else None) or (old.user if old else None)
    if not user:
        return
    gone = new is None or new.status in (enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED)

    # blocks / unblocks of the bot (private chat) are logged by bot_stopped_update below
    if getattr(update.chat, "type", None) == enums.ChatType.PRIVATE:
        return

    entries = await get_force_channel_docs()
    is_force_chat = any(_numeric_id(e) == update.chat.id for e in entries)
    if not gone:
        return

    clear_user_cache(user.id)
    if any(e["mode"] == "request" and _numeric_id(e) == update.chat.id for e in entries):
        await clear_join_request(user.id, update.chat.id)
