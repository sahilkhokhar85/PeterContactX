import asyncio
import html
import time
from collections import deque

from pyrogram import Client, filters, enums
from pyrogram.errors import UserIsBlocked, InputUserDeactivated
from pyrogram.types import Message

from config import (
    OWNERS, RATE_LIMIT_MESSAGES, RATE_LIMIT_SECONDS,
    INDEX_MIN_SCORE, INDEX_MIN_QUERY_CHARS,
)
from database import (
    add_user, is_banned, get_admins, admin_filter, find_auto_reply,
    search_content,
    save_forward_map, get_forward_user, save_link, get_links,
)
from logchannel import log_user_message, log_admin_reply, log_event, once_per, clip, with_reply
from Plugins.forcesub import is_subscribed, send_force_sub_prompt

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
        if once_per(("rate", user_id), 300):
            log_event(client, "⏳ Rate Limited (Too Fast)", user=message.from_user, message=message)
        if notice:
            await message.reply("<b>⏳ You Are Sending Messages Too Fast. Please Slow Down.</b>", parse_mode=enums.ParseMode.HTML)
        return

    if not await is_subscribed(client, user_id):
        log_event(client, "📢 Force Join Pending (Message Not Delivered)", user=message.from_user, message=message)
        return await send_force_sub_prompt(client, message)

    await add_user(user_id, message.from_user.first_name or "", message.from_user.username or "")
    log_user_message(client, message)   # log channel (no-op when LOG_CHANNEL is not set)

    # Automatic keyword reply — replaces forwarding when a keyword matches.
    if message.text:
        auto_reply = await find_auto_reply(message.text)
        if auto_reply:
            log_event(client, "🤖 Auto Reply Sent", user=message.from_user,
                      lines=[f"<b>💬 Reply:</b> {clip(auto_reply)}"])
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
            await save_link(user_id, message.id, admin_id, fwd.id, "user")   # for edit sync
            sent_to_any = True
        except Exception:
            continue

    if not sent_to_any:
        log_event(client, "❌ Message Could Not Be Forwarded To Any Admin", user=message.from_user)
        return await message.reply("<b>❌ Error: Message Could Not Be Sent.</b>", parse_mode=enums.ParseMode.HTML)

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
                log_event(client, "🗂 Indexed Post Sent To User", user=message.from_user,
                          lines=[f"<b>📄 Post:</b> {clip(reply_text)}"])
                return
            except Exception:
                pass  # source post may be deleted/inaccessible — fall through below

    sent_msg = await message.reply(
        "<b>✅ Message Sent!</b>",
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
        log_event(client, "❌ Reply Not Delivered (User Not Detected)", by=message.from_user, message=message)
        return await message.reply("<b>❌ User ID Not Detected. Reply To A Message The Bot Forwarded To You.</b>", parse_mode=enums.ParseMode.HTML)
    try:
        copied = await message.copy(user_id)
        await save_link(message.chat.id, message.id, user_id, copied.id, "admin")   # for edit sync
        log_admin_reply(client, message, user_id)
    except UserIsBlocked:
        log_event(client, "🚫 Reply Failed (User Blocked The Bot)", uid=user_id, by=message.from_user, message=message)
        await message.reply("<b>🚫 This User Has Blocked The Bot.</b>", parse_mode=enums.ParseMode.HTML)
    except InputUserDeactivated:
        log_event(client, "🚫 Reply Failed (Account Deleted)", uid=user_id, by=message.from_user, message=message)
        await message.reply("<b>🚫 This User's Account Is Deleted.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        log_event(client, "⚠️ Reply Failed", uid=user_id, by=message.from_user,
                  lines=[f"<b>Error:</b> <code>{html.escape(str(e))}</code>"], message=message)
        await message.reply(
            f"<b>⚠️ Error Sending To User:</b>\n<code>{html.escape(str(e))}</code>",
            parse_mode=enums.ParseMode.HTML
        )


# ---------------- Edits ----------------

@Client.on_edited_message(filters.private & admin_filter)
async def admin_edited(client: Client, message: Message):
    """The admin edited a reply that was sent to a user: update that user's copy."""
    for link in await get_links(message.chat.id, message.id):
        if link.get("kind") != "admin":
            continue
        log_admin_reply(client, message, link["peer_chat"], "✏️ Reply Edited")
        try:
            if message.text:
                await client.edit_message_text(link["peer_chat"], link["peer_msg"], message.text.html, parse_mode=enums.ParseMode.HTML)
            elif message.caption is not None:
                await client.edit_message_caption(link["peer_chat"], link["peer_msg"], message.caption.html, parse_mode=enums.ParseMode.HTML)
            else:
                continue
        except Exception as e:
            if "MESSAGE_NOT_MODIFIED" in str(e).upper():
                continue
            log_event(client, "⚠️ Reply Edit Failed", uid=link["peer_chat"], by=message.from_user,
                      lines=[f"<b>Error:</b> <code>{html.escape(str(e))}</code>"])
            await with_reply(
                client.send_message,
                message.chat.id,
                f"<b>⚠️ Couldn't Edit The Message For The User:</b>\n<code>{html.escape(str(e))}</code>",
                parse_mode=enums.ParseMode.HTML, reply_id=message.id,
            )


@Client.on_edited_message(filters.private & ~admin_filter)
async def user_edited(client: Client, message: Message):
    """The user edited a message that was forwarded to the admins: forward the new version again, marked as edited."""
    if not message.from_user:
        return
    user_id = message.from_user.id
    if await is_banned(user_id):
        return
    log_user_message(client, message, "✏️ Edited Message")
    links = [l for l in await get_links(user_id, message.id) if l.get("kind") == "user"]
    if not links:
        return
    allowed, _ = check_rate_limit(user_id)
    if not allowed:
        return
    for link in links:
        admin_id = link["peer_chat"]
        try:
            await with_reply(
                client.send_message,
                admin_id, "<b>✏️ Edited Message !! 👇</b>",
                parse_mode=enums.ParseMode.HTML, reply_id=link["peer_msg"],
            )
            fwd = await message.forward(admin_id)
            await save_forward_map(admin_id, fwd.id, user_id)
            await save_link(user_id, message.id, admin_id, fwd.id, "user")   # keeps the newest copy linked for further edits
        except Exception:
            continue
