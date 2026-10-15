"""Broadcast.

Two ways to start a broadcast, both using the same fast engine:

1. /settings → Broadcast  (button builder: media / text with {mention} / buttons / pin)
2. /broadcast             (send ANY messages: text, photo, video, file, sticker, whole albums...)
       /broadcast → send your messages one by one → /done → you get a PREVIEW → Confirm
       /broadcast as a reply to a message (or album) goes straight to the preview.
       /cancel stops the flow at any time.
   /send <user_id>        same flow, but the messages go to ONE user only.

Engine: many users in parallel (BROADCAST_CONCURRENCY), FloodWait is waited out and
retried, users who blocked the bot / deleted their account are removed from the user
list, banned users never receive anything, only one broadcast runs at a time, and the
admin sees live progress with a Stop button.
"""
import asyncio
import datetime
import html
import secrets
import time

from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked, PeerIdInvalid
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import BROADCAST_CONCURRENCY
from database import admin_perm, get_broadcast_targets, delete_user, is_banned
from Plugins.contact import build_buttons_markup

IDLE_SECONDS = 600        # stop waiting for more messages after 10 minutes of silence
ALBUM_QUIET = 1.2         # seconds of silence that mean "the album is complete"
MAX_MESSAGES = 50         # safety limit for one /broadcast
JOB_LIFETIME = 1800       # an unconfirmed preview expires after 30 minutes
PROGRESS_EVERY = 3        # seconds between progress edits (Telegram rate-limits edits)

_WAITING = {}   # admin_id -> {"t": last activity, "msgs": [Message], "ack": Task|None, "target": int|None}
_JOBS = {}      # token -> {"admin": id, "units": [[Message]], "target": int|None, "t": created}
_BG = set()
_run_lock = None
_stop_event = None


def _lock() -> asyncio.Lock:
    global _run_lock
    if _run_lock is None:
        _run_lock = asyncio.Lock()
    return _run_lock


def _stop() -> asyncio.Event:
    global _stop_event
    if _stop_event is None:
        _stop_event = asyncio.Event()
    return _stop_event


def is_running() -> bool:
    return _run_lock is not None and _run_lock.locked()


def _spawn(coro):
    task = asyncio.create_task(coro)
    _BG.add(task)
    task.add_done_callback(_BG.discard)
    return task


# ============================================================
# ENGINE
# ============================================================

async def _send_with_retry(send_one, user_id: int, name: str):
    """Returns (ok, reason). reason: Success / Blocked / Deleted / Error."""
    flood_retries = 0
    while True:
        try:
            await send_one(user_id, name)
            return True, "Success"
        except FloodWait as e:
            flood_retries += 1
            if flood_retries > 5:
                return False, "Error"
            await asyncio.sleep(e.value + 1)
        except UserIsBlocked:
            await delete_user(user_id)
            return False, "Blocked"
        except InputUserDeactivated:
            await delete_user(user_id)
            return False, "Deleted"
        except PeerIdInvalid:
            # NOT "blocked": it usually means the bot's peer cache doesn't know this
            # user yet (common right after a redeploy). Never delete users for it.
            return False, "Error"
        except Exception:
            return False, "Error"


def _progress_text(title, total, done, counts, started, finished=False, stopped=False):
    elapsed = datetime.timedelta(seconds=int(time.time() - started))
    if finished:
        head = f"**⛔ {title} stopped**" if stopped else f"**✅ {title} completed**"
        head += f"\n_Time taken: {elapsed}_"
    else:
        head = f"**📢 {title} in progress...**\n_Elapsed: {elapsed}_"
    return (
        f"{head}\n\n"
        f"• Total users: `{total}`\n"
        f"• Done: `{done}` / `{total}`\n"
        f"• Sent: `{counts['success']}`\n"
        f"• Blocked the bot: `{counts['blocked']}`\n"
        f"• Deleted account: `{counts['deleted']}`\n"
        f"• Failed: `{counts['failed']}`"
    )


async def run_broadcast(client, status, targets: list, send_one, title: str = "Broadcast"):
    """targets: [{user_id, first_name}]. send_one(user_id, first_name) sends one user's copy and raises on failure."""
    async with _lock():
        _stop().clear()
        total = len(targets)
        counts = {"success": 0, "blocked": 0, "deleted": 0, "failed": 0}
        done = 0
        started = time.time()
        stop_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Stop", callback_data="bc_stop")]])

        queue = asyncio.Queue()
        for t in targets:
            queue.put_nowait(t)

        async def worker():
            nonlocal done
            while not _stop().is_set():
                try:
                    t = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                ok, reason = await _send_with_retry(send_one, t["user_id"], t["first_name"])
                if ok:
                    counts["success"] += 1
                elif reason == "Blocked":
                    counts["blocked"] += 1
                elif reason == "Deleted":
                    counts["deleted"] += 1
                else:
                    counts["failed"] += 1
                done += 1

        async def reporter():
            while True:
                await asyncio.sleep(PROGRESS_EVERY)
                try:
                    await status.edit_text(_progress_text(title, total, done, counts, started), reply_markup=stop_markup)
                except Exception:
                    pass

        try:
            await status.edit_text(_progress_text(title, total, 0, counts, started), reply_markup=stop_markup)
        except Exception:
            pass

        progress_task = asyncio.create_task(reporter())
        try:
            await asyncio.gather(*[worker() for _ in range(max(1, min(BROADCAST_CONCURRENCY, total)))])
        finally:
            progress_task.cancel()

        stopped = _stop().is_set() and done < total
        try:
            await status.edit_text(_progress_text(title, total, done, counts, started, finished=True, stopped=stopped))
        except Exception:
            pass
        return counts


@Client.on_callback_query(filters.regex("^bc_stop$") & admin_perm("can_broadcast"))
async def bc_stop_cb(client, query):
    if not is_running():
        return await query.answer("No broadcast is running.", show_alert=True)
    _stop().set()
    await query.answer("Stopping... users already in progress will finish.", show_alert=True)


# ============================================================
# SENDERS
# ============================================================

def make_draft_sender(client, draft: dict):
    """Sender for the /settings → Broadcast builder (media / text / buttons / pin, {mention} per user)."""
    markup = build_buttons_markup(draft["buttons"])

    async def send_one(user_id, name):
        mention = f'<a href="tg://user?id={user_id}">{html.escape(name or "there")}</a>'
        text = (draft["text"] or "").replace("{mention}", mention)
        if draft["media_type"] == "photo":
            sent = await client.send_photo(user_id, draft["media_file_id"], caption=text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        elif draft["media_type"] == "video":
            sent = await client.send_video(user_id, draft["media_file_id"], caption=text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        else:
            sent = await client.send_message(user_id, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        if draft["pin"]:
            try:
                await sent.pin()
            except Exception:
                pass

    return send_one


def _to_units(msgs):
    """Messages -> ordered units. A unit is one message, or one whole album (same media_group_id)."""
    units = []
    last_gid = None
    for m in sorted(msgs, key=lambda m: m.id):
        gid = getattr(m, "media_group_id", None)
        if gid and units and gid == last_gid:
            units[-1].append(m)
        else:
            units.append([m])
        last_gid = gid
    return units


async def _deliver_unit(client, user_id, unit):
    """Copy one message, or one whole album, to user_id (no 'Forwarded from')."""
    if len(unit) == 1:
        await unit[0].copy(chat_id=user_id)
        return
    try:
        await client.copy_media_group(user_id, unit[0].chat.id, unit[0].id)
    except FloodWait:
        raise
    except Exception:
        for m in unit:   # fallback: same content, just not grouped as an album
            await m.copy(chat_id=user_id)


def make_copy_sender(client, units):
    """Sender for /broadcast and /send: every unit in order. A FloodWait retries only the unit that waited, so nothing is sent twice."""
    async def send_one(user_id, name):
        for unit in units:
            flood_retries = 0
            while True:
                try:
                    await _deliver_unit(client, user_id, unit)
                    break
                except FloodWait as e:
                    flood_retries += 1
                    if flood_retries > 5:
                        raise
                    await asyncio.sleep(e.value + 1)
    return send_one


# ============================================================
# /broadcast and /send  (copy mode)
# ============================================================

def _clean_old_jobs():
    now = time.time()
    for token in [t for t, j in _JOBS.items() if now - j["t"] > JOB_LIFETIME]:
        _JOBS.pop(token, None)


async def _make_preview(bot, admin_id, chat_id, units, target=None):
    """Shows the admin exactly what will be sent (in order), then Confirm / Cancel."""
    try:
        for unit in units:
            await _deliver_unit(bot, chat_id, unit)   # the very same code the real broadcast uses
    except Exception as e:
        return await bot.send_message(
            chat_id,
            f"**❌ One of these messages can't be broadcast.**\n`{type(e).__name__}`\n\n"
            "Send /broadcast again without that message."
        )

    _clean_old_jobs()
    token = secrets.token_hex(4)
    _JOBS[token] = {"admin": admin_id, "units": units, "target": target, "t": time.time()}
    n = len(units)
    if n == 1 and len(units[0]) == 1:
        what = "1 message"
    elif n == 1:
        what = f"1 album of {len(units[0])} items"
    else:
        what = f"{n} messages"
    order = " in this order" if n > 1 else ""
    if target:
        who = f"ONLY to user `{target}`"
        ask, yes = "Is this exactly what you want to send?", "✅ Confirm send"
    else:
        who = f"to `{len(await get_broadcast_targets())}` users"
        ask, yes = "Is this exactly what you want to broadcast?", "✅ Confirm broadcast"
    await bot.send_message(
        chat_id,
        f"**👆 Preview: {what} will be sent {who}{order}.**\n\n**{ask}**",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(yes, callback_data=f"bc_yes:{token}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"bc_no:{token}"),
        ]]),
    )


async def _messages_from_reply(bot, replied):
    if replied.media_group_id:
        try:
            return await bot.get_media_group(replied.chat.id, replied.id)
        except Exception:
            pass
    return [replied]


def _reset_waiting(admin_id):
    old = _WAITING.pop(admin_id, None)
    if old and old.get("ack"):
        old["ack"].cancel()


@Client.on_message(filters.command("broadcast") & filters.private & admin_perm("can_broadcast"))
async def broadcast_cmd(bot, message):
    admin_id = message.from_user.id
    _reset_waiting(admin_id)

    if message.reply_to_message:
        msgs = await _messages_from_reply(bot, message.reply_to_message)
        return await _make_preview(bot, admin_id, message.chat.id, _to_units(msgs))

    _WAITING[admin_id] = {"t": time.time(), "msgs": [], "ack": None, "target": None}
    await message.reply_text(
        "**📣 Send the messages you want to broadcast, one by one.**\n"
        "Text, photo, video, file, sticker, a whole album, anything works.\n\n"
        "/done when finished (you get a preview first)\n"
        "/cancel to stop"
    )


@Client.on_message(filters.command("send") & filters.private & admin_perm("can_broadcast"))
async def send_cmd(bot, message):
    """/send <user_id>: same flow as /broadcast, but only ONE user gets it."""
    admin_id = message.from_user.id
    _reset_waiting(admin_id)

    arg = message.command[1] if len(message.command) > 1 else ""
    if not (arg.isdigit() and 5 <= len(arg) <= 16):
        return await message.reply_text(
            "**Usage:**\n`/send user_id`, then send your messages, then /done\n"
            "Or reply to a message with `/send user_id`"
        )
    target = int(arg)
    if await is_banned(target):
        return await message.reply_text("**🚫 This user is banned.**\nUse /unban first if you really want to message them.")

    if message.reply_to_message:
        msgs = await _messages_from_reply(bot, message.reply_to_message)
        return await _make_preview(bot, admin_id, message.chat.id, _to_units(msgs), target)

    _WAITING[admin_id] = {"t": time.time(), "msgs": [], "ack": None, "target": target}
    await message.reply_text(
        f"**📨 Send the messages for user** `{target}`**, one by one.**\n"
        "Text, photo, video, file, sticker, a whole album, anything works.\n\n"
        "/done when finished (you get a preview first)\n"
        "/cancel to stop"
    )


async def _ack(admin_id, chat_id, bot, delay):
    if delay:
        await asyncio.sleep(delay)
    state = _WAITING.get(admin_id)
    if not state:
        return
    units = _to_units(state["msgs"])
    await bot.send_message(
        chat_id,
        f"**✅ Added. {len(units)} message(s) ready.**\n"
        "Send more, or /done to preview, /cancel to stop.",
    )


# group=-2: runs before every other plugin, so while the bot is collecting the
# broadcast, whatever the admin sends (an album's items included) is captured
# here and does not trigger other features (e.g. being sent to a user).
@Client.on_message(filters.private & filters.incoming, group=-2)
async def _capture_broadcast_message(bot, message):
    user = message.from_user
    state = _WAITING.get(user.id) if user else None
    if not state:
        return
    if time.time() - state["t"] > IDLE_SECONDS:
        _reset_waiting(user.id)
        return

    first_word = ((message.text or "").strip().split(maxsplit=1) or [""])[0].split("@")[0].lower()
    if first_word in ("/broadcast", "/send"):
        return   # lets the command restart the flow

    state["t"] = time.time()

    if first_word == "/cancel":
        _reset_waiting(user.id)
        await message.reply_text("**Cancelled.**")
        raise StopPropagation

    if first_word == "/done":
        if state["ack"]:
            state["ack"].cancel()
        if not state["msgs"]:
            await message.reply_text("**Nothing added yet. Send your message or /cancel.**")
            raise StopPropagation
        _WAITING.pop(user.id, None)
        await _make_preview(bot, user.id, message.chat.id, _to_units(state["msgs"]), state.get("target"))
        raise StopPropagation

    if len(state["msgs"]) >= MAX_MESSAGES:
        await message.reply_text(f"**Limit reached ({MAX_MESSAGES} messages). Send /done or /cancel.**")
        raise StopPropagation

    state["msgs"].append(message)
    if state["ack"]:
        state["ack"].cancel()
    # a single message is acknowledged at once, an album after its last item arrived
    state["ack"] = _spawn(_ack(user.id, message.chat.id, bot, ALBUM_QUIET if message.media_group_id else 0))
    raise StopPropagation


@Client.on_callback_query(filters.regex(r"^bc_(yes|no):[0-9a-f]{8}$") & admin_perm("can_broadcast"))
async def broadcast_answer(bot, query):
    action, _, token = query.data.partition(":")
    job = _JOBS.get(token)

    if not job:
        await query.answer("This preview expired. Run /broadcast again.", show_alert=True)
        try:
            await query.message.edit_text("**⌛ Preview expired. Run /broadcast again.**")
        except Exception:
            pass
        return
    if job["admin"] != query.from_user.id:
        return await query.answer("This broadcast belongs to another admin.", show_alert=True)

    if action == "bc_no":
        _JOBS.pop(token, None)
        await query.answer("Cancelled.")
        return await query.message.edit_text("**❌ Cancelled. Nothing was sent.**")

    if job.get("target"):
        _JOBS.pop(token, None)   # one confirm = one send
        await query.answer("Sending...")
        return await _run_direct(bot, query.message, job["target"], job["units"])

    if is_running():
        return await query.answer("A broadcast is already running. Wait for it to finish.", show_alert=True)

    _JOBS.pop(token, None)   # one confirm = one broadcast (a second tap finds nothing)
    await query.answer("Broadcasting...")
    targets = await get_broadcast_targets()
    await run_broadcast(bot, query.message, targets, make_copy_sender(bot, job["units"]))


async def _run_direct(bot, status, user_id, units):
    """Send to one user and tell the admin exactly what happened."""
    await status.edit_text("**Sending...**")
    sent = 0
    reason = None
    try:
        for unit in units:
            while True:
                try:
                    await _deliver_unit(bot, user_id, unit)
                    break
                except FloodWait as e:
                    await asyncio.sleep(e.value + 1)
            sent += 1
    except UserIsBlocked:
        reason = "That user has blocked the bot."
    except InputUserDeactivated:
        reason = "That account is deleted."
    except PeerIdInvalid:
        reason = "The bot can't find this user. Check the ID. The user must have started the bot."
    except Exception as e:
        reason = f"Failed ({type(e).__name__})."

    if reason is None:
        return await status.edit_text(f"**✅ Sent to user** `{user_id}`**. ({sent} message(s))**")
    await status.edit_text(
        f"**❌ Not sent to user** `{user_id}`\n**{reason}**\n"
        f"Delivered before the error: {sent} of {len(units)}"
    )
