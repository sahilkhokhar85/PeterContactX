import asyncio
import time
from collections import deque

from pyrogram import Client, filters, enums
from pyrogram.errors import UserIsBlocked, InputUserDeactivated
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    OWNERS, RATE_LIMIT_MESSAGES, RATE_LIMIT_SECONDS,
    INDEX_MIN_SCORE, INDEX_MIN_QUERY_CHARS,
)
from database import (
    add_user, is_banned, get_admins, admin_filter, find_auto_reply,
    get_start_message, get_chat_history, add_chat_turns, search_content,
    save_forward_map, get_forward_user,
)
from ai import ask_gemini
# force-join lives in Plugins/forcesub.py; re-exported here because other plugins import these names from contact
from Plugins.forcesub import (  # noqa: F401
    get_not_joined_channels, is_subscribed, force_sub_markup, send_force_sub_prompt,
)

SENT_CONFIRM_DELETE_AFTER = 3  # seconds before "Message sent!" auto-deletes

# Admin commands. A reply that carries one of these is a command (e.g. replying
# "/ban" to a user's forwarded message), NOT text to be sent to the user.
ADMIN_COMMANDS = [
    "settings", "menu", "restart", "clearindex",
    "broadcast", "send", "done", "cancel",
    "ban", "unban", "banlist", "status", "info",
]


# ---------------- Anti-spam ----------------

_hits = {}          # user_id -> deque of recent message timestamps
_last_group = {}    # user_id -> media_group_id of the last album (an album counts as ONE message)
_warned_at = {}     # user_id -> last time the "slow down" notice was sent


def check_rate_limit(user_id: int, media_group_id=None):
    """Returns (allowed, send_notice). RATE_LIMIT_MESSAGES <= 0 disables the limit."""
    if RATE_LIMIT_MESSAGES <= 0:
        return True, False
    if media_group_id and _last_group.get(user_id) == media_group_id:
        return True, False
    now = time.time()
    hits = _hits.setdefault(user_id, deque())
    while hits and now - hits[0] > RATE_LIMIT_SECONDS:
        hits.popleft()
    if len(hits) >= RATE_LIMIT_MESSAGES:
        notice = now - _warned_at.get(user_id, 0) > RATE_LIMIT_SECONDS
        if notice:
            _warned_at[user_id] = now
        return False, notice
    hits.append(now)
    if media_group_id:
        _last_group[user_id] = media_group_id
    if len(_hits) > 10000:   # keep memory bounded
        for uid in [u for u, h in _hits.items() if not h or now - h[-1] > RATE_LIMIT_SECONDS]:
            _hits.pop(uid, None)
            _warned_at.pop(uid, None)
            _last_group.pop(uid, None)
    return True, False


# ---------------- User → admins ----------------

@Client.on_message(filters.private & ~admin_filter & ~filters.command("start"))
async def forward_to_admin(client: Client, message: Message):
    user_id = message.from_user.id

    if await is_banned(user_id):
        return

    allowed, notice = check_rate_limit(user_id, message.media_group_id)
    if not allowed:
        if notice:
            await message.reply("⏳ <i>You are sending messages too fast. Please slow down.</i>", parse_mode=enums.ParseMode.HTML)
        return

    if not await is_subscribed(client, user_id):
        return await send_force_sub_prompt(client, message)

    await add_user(user_id, message.from_user.first_name or "", message.from_user.username or "")

    # Automatic keyword reply — replaces forwarding when a keyword matches.
    if message.text:
        auto_reply = await find_auto_reply(message.text)
        if auto_reply:
            return await message.reply(auto_reply, parse_mode=enums.ParseMode.HTML)

    admin_ids = set(await get_admins())
    admin_ids.update(OWNERS)

    sent_to_any = False
    for admin_id in admin_ids:
        try:
            fwd = await message.forward(admin_id)
            # kept in Mongo (not RAM) so the admin can still reply after a restart,
            # even if the user hides their identity on forwarded messages
            await save_forward_map(admin_id, fwd.id, user_id)
            sent_to_any = True
        except Exception:
            continue

    if not sent_to_any:
        return await message.reply("❌ **Error:** Message could not be sent.")

    # Indexed content match — if the user's text matches something scanned
    # from a registered channel (see Plugins/indexer.py), send that directly
    # instead of a generated AI reply. Short texts and weak matches are ignored.
    text = (message.text or "").strip()
    if text and not text.startswith("/") and len(text) >= INDEX_MIN_QUERY_CHARS:
        matches = await search_content(text, limit=1, min_score=INDEX_MIN_SCORE)
        if matches:
            match = matches[0]
            reply_text = match.get("text") or ""
            try:
                if match.get("has_media"):
                    await client.copy_message(
                        chat_id=user_id,
                        from_chat_id=match["chat_id"],
                        message_id=match["message_id"],
                        caption=reply_text
                    )
                else:
                    await message.reply(reply_text)
                return
            except Exception:
                pass  # source post may be deleted/inaccessible — fall through below

    # AI assistant reply — only for plain text, only when GEMINI_API_KEY is
    # configured (see config.py). Admin still gets the forward above either way.
    if message.text:
        history = await get_chat_history(user_id)
        ai_reply = await ask_gemini(history, message.text)
        if ai_reply:
            await add_chat_turns(user_id, message.text, ai_reply)
            return await message.reply(ai_reply)

    sent_msg = await message.reply(
        "✅ <i>Message sent!</i>",
        parse_mode=enums.ParseMode.HTML
    )
    asyncio.create_task(_delete_after_delay(sent_msg))


async def _delete_after_delay(msg: Message):
    await asyncio.sleep(SENT_CONFIRM_DELETE_AFTER)
    try:
        await msg.delete()
    except Exception:
        pass


# ---------------- Admin → user ----------------

async def resolve_reply_target(admin_id: int, replied_msg: Message):
    """Which user does this forwarded message belong to? Mongo map first, forward header as a fallback."""
    user_id = await get_forward_user(admin_id, replied_msg.id)
    if user_id:
        return user_id
    sender = getattr(replied_msg, "forward_from", None)
    return sender.id if sender else None


@Client.on_message(filters.private & admin_filter & filters.reply & ~filters.command(ADMIN_COMMANDS))
async def reply_to_user(client: Client, message: Message):
    user_id = await resolve_reply_target(message.chat.id, message.reply_to_message)
    if not user_id:
        return await message.reply("❌ **User ID not detected.** Reply to a message the bot forwarded to you.")
    try:
        await message.copy(user_id)
    except UserIsBlocked:
        await message.reply("🚫 **This user has blocked the bot.**")
    except InputUserDeactivated:
        await message.reply("🚫 **This user's account is deleted.**")
    except Exception as e:
        await message.reply(f"⚠️ **Error sending to user:**\n`{e}`")


# ---------------- Start message helpers ----------------

def build_buttons_markup(rows):
    if not rows:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(b["text"], url=b["url"]) for b in row] for row in rows])


async def send_start_message(client: Client, chat_id: int, mention: str):
    """Sends the admin-configurable /start message (used by /start and settings preview)."""
    doc = await get_start_message()
    text = doc.get("text", "").replace("{mention}", mention)
    markup = build_buttons_markup(doc.get("buttons") or [])
    media_type = doc.get("media_type")
    media_file_id = doc.get("media_file_id")
    if media_type == "photo":
        await client.send_photo(chat_id, media_file_id, caption=text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
    elif media_type == "video":
        await client.send_video(chat_id, media_file_id, caption=text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
    else:
        await client.send_message(chat_id, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
