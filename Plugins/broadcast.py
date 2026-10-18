"""Broadcast and /send, same flow as the ChillFlizX file store bot.

/broadcast flow:
  1. /broadcast  -> the bot waits (only a Cancel button), send as many messages as you want, one by one
                    (text, photo, video, file, sticker, a whole album, anything)
  2. After the first message the bot shows "Ready" with Done / Cancel buttons.
     Done -> a PREVIEW of everything, in order, with Confirm / Cancel
  3. only Confirm starts the broadcast.
  /broadcast as a REPLY to a message (or album) skips step 1-2 and goes straight to the preview.

/send <user_id> works exactly the same way, but the messages go ONLY to that one user.
"""
import asyncio
import html
import secrets
import time

from pyrogram import Client, filters, StopPropagation
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked, PeerIdInvalid
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import BROADCAST_CONCURRENCY
from logchannel import log_text
from database import admin_perm, get_broadcast_targets, delete_user, is_banned, get_user

# How many users we message at once. Telegram's flood limits are mostly per chat,
# so sending to many *different* users concurrently is safe and much faster.
CONCURRENCY = BROADCAST_CONCURRENCY

IDLE_SECONDS = 600        # the bot stops waiting if you send nothing for 10 minutes
ALBUM_QUIET = 1.2         # seconds of silence that mean "the album is complete"
MAX_MESSAGES = 50         # safety limit for one broadcast
JOB_LIFETIME = 1800       # an unconfirmed preview expires after 30 minutes
FLOOD_RETRIES = 5         # how often one message is retried after a FloodWait

_WAITING = {}   # admin_id -> {"t": last activity, "msgs": [Message], "ack": Task|None, "target": int|None}
_JOBS = {}      # token -> {"admin": id, "units": [[Message]], "target": int|None, "t": created}
_BG = set()
_run_lock = None


def _lock() -> asyncio.Lock:
    """Created lazily so it belongs to the running event loop."""
    global _run_lock
    if _run_lock is None:
        _run_lock = asyncio.Lock()
    return _run_lock


def _spawn(coro):
    task = asyncio.create_task(coro)
    _BG.add(task)
    task.add_done_callback(_BG.discard)
    return task


def _to_units(msgs):
    """Messages -> ordered list of units. A unit is one message, or one whole
    album (consecutive messages with the same media_group_id)."""
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
    """Send one message, or one whole album, to user_id as a copy (no 'Forwarded from')."""
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


async def _deliver_with_retry(client, user_id, unit):
    """One unit, FloodWait waited out. Only the unit that waited is retried, so nothing is sent twice."""
    retries = 0
    while True:
        try:
            return await _deliver_unit(client, user_id, unit)
        except FloodWait as e:
            retries += 1
            if retries > FLOOD_RETRIES:
                raise
            await asyncio.sleep(e.value + 1)


async def broadcast_messages(client, user_id, units):
    """Send every unit to one user, in order. Returns (ok, reason)."""
    try:
        for unit in units:
            await _deliver_with_retry(client, user_id, unit)
        return True, "Success"
    except InputUserDeactivated:
        await delete_user(int(user_id))
        return False, "Deleted"
    except UserIsBlocked:
        await delete_user(int(user_id))
        return False, "Blocked"
    except PeerIdInvalid:
        # NOTE: PeerIdInvalid does NOT mean the user blocked/deleted the bot.
        # It usually means the bot's local peer cache doesn't have this user yet
        # (common right after a redeploy). Never delete the user for it.
        return False, "Error"
    except Exception:
        return False, "Error"


def _clean_old_jobs():
    now = time.time()
    for token in [t for t, j in _JOBS.items() if now - j["t"] > JOB_LIFETIME]:
        _JOBS.pop(token, None)


async def _user_label(bot, uid):
    """'Name (id)' for the /send messages. The name comes from the saved users, else from Telegram;
    if neither knows it, only the id is shown."""
    name = ""
    try:
        doc = await get_user(int(uid))
        name = str((doc or {}).get("first_name") or "").strip()
    except Exception:
        pass
    if not name:
        try:
            u = await bot.get_users(int(uid))
            name = " ".join(x for x in (u.first_name, u.last_name) if x).strip()
        except Exception:
            pass
    if name:
        return f"<b>{html.escape(name)} (</b><code>{uid}</code><b>)</b>"
    return f"<code>{uid}</code>"


def _users(n):
    return "User" if n == 1 else "Users"


# ---------------- text helpers (every line bold, like the file store bot) ----------------

def _bold(lines):
    """Every non-empty line bold. The progress bar line is <code> (monospace)."""
    return "\n".join("" if not l else (l if l.startswith("<code>") else f"<b>{l}</b>") for l in lines)


def _bar(done, total, width=10):
    pct = int(done * 100 / total) if total else 0
    filled = round(width * pct / 100)
    return "▰" * filled + "▱" * (width - filled), pct


def _bar_line(done, total):
    bar, pct = _bar(done, total)
    return f"<code>{bar}</code> <b>{pct}%</b>"


def _dur(sec):
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")


def _bot_text(c, total, final, secs):
    ok = int(c["success"] * 100 / total) if total else 0
    return _bold([
        "✅ BROADCAST COMPLETED !!" if final else "📣 BROADCASTING…",
        "",
        _bar_line(c["done"], total),
        f"⏱ Time: {_dur(secs)}",
        "",
        "📊 SUMMARY",
        f"👥 Total: {total}",
        f"✅ Success: {c['success']} ({ok}%)",
        f"🚫 Blocked: {c['blocked']}",
        f"🗑 Deleted: {c['deleted']}",
        f"❌ Failed: {c['failed']}",
    ])


# ---------------- preview ----------------

async def _make_preview(bot, admin_id, chat_id, units, target=None):
    """Show the admin exactly what will be sent (in order), then Confirm / Cancel."""
    try:
        for unit in units:
            await _deliver_unit(bot, chat_id, unit)   # the very same code the real broadcast uses
    except Exception as e:
        return await bot.send_message(
            chat_id,
            "<b>❌ One Of These Messages Can't Be Broadcast.</b>\n"
            f"<code>{type(e).__name__}</code>\n\n"
            "<b>Send /broadcast Again Without That Message.</b>",
        )

    _clean_old_jobs()
    token = secrets.token_hex(4)
    _JOBS[token] = {"admin": admin_id, "units": units, "target": target, "t": time.time()}
    n = len(units)
    what = "1 Message" if n == 1 and len(units[0]) == 1 else (
        f"1 Album · {len(units[0])} Items" if n == 1 else f"{n} Messages (In Order)")
    cancel = [InlineKeyboardButton("❌ Cancel", callback_data=f"bc_no:{token}")]

    if target:
        text = (
            "<b>👆 SEND PREVIEW !!</b>\n\n"
            f"<b>📦 Content: {what}</b>\n\n"
            f"<b>👤 User:</b> {await _user_label(bot, target)} <b>only</b>\n\n"
            "<b>⚠️ Check Once — Sending can't be undone.</b>"
        )
        buttons = [InlineKeyboardButton("✅ Confirm Send", callback_data=f"bc_yes:{token}")]
        return await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup([buttons, cancel]))

    mine = len(await get_broadcast_targets())
    text = _bold([
        "👆 BROADCAST PREVIEW !!",
        "",
        f"📦 Content: {what}",
        "",
        f"👥 Send To: {mine} {_users(mine)}",
        "",
        "⚠️ Check Once — Sending can't be undone.",
    ])
    buttons = [InlineKeyboardButton("✅ Confirm Broadcast", callback_data=f"bc_yes:{token}")]
    await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup([buttons, cancel]))


async def _messages_from_reply(bot, replied):
    if replied.media_group_id:
        try:
            return await bot.get_media_group(replied.chat.id, replied.id)
        except Exception:
            pass
    return [replied]


def _reset_waiting(admin_id):
    old = _WAITING.pop(admin_id, None)
    if old:
        if old.get("ack"):
            old["ack"].cancel()
        _spawn(_close_collect(old))


async def _close_collect(state, keep_id=None):
    """Remove the collecting messages (the prompt and the 'Ready' message) from the chat."""
    for key in ("prompt_msg", "ack_msg"):
        msg = state.get(key)
        if msg and getattr(msg, "id", None) != keep_id:
            await _drop(msg)


async def _strip_prompt(state):
    """After the first message the prompt loses its Cancel button (Cancel now lives under 'Ready')."""
    msg = state.get("prompt_msg")
    if msg and not state.get("prompt_stripped"):
        state["prompt_stripped"] = True
        try:
            await msg.edit_reply_markup(None)
        except Exception:
            pass


def _cancel_markup():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="bcw_cancel")]])


# ---------------- /broadcast and /send ----------------

@Client.on_message(filters.command("broadcast") & filters.private & admin_perm("can_broadcast"))
async def broadcast_cmd(bot, message):
    admin_id = message.from_user.id
    _reset_waiting(admin_id)

    if message.reply_to_message:
        msgs = await _messages_from_reply(bot, message.reply_to_message)
        return await _make_preview(bot, admin_id, message.chat.id, _to_units(msgs))

    state = _WAITING[admin_id] = {"t": time.time(), "msgs": [], "ack": None, "target": None}
    state["prompt_msg"] = await message.reply_text(
        "<b>📣 Send Your Messages One By One.</b>", reply_markup=_cancel_markup()
    )


@Client.on_message(filters.command("send") & filters.private & admin_perm("can_broadcast"))
async def send_cmd(bot, message):
    """/send <user_id>: same flow as /broadcast, but only ONE user gets it."""
    admin_id = message.from_user.id
    _reset_waiting(admin_id)

    arg = message.command[1] if len(message.command) > 1 else ""
    if not (arg.isdigit() and 5 <= len(arg) <= 16):
        return await message.reply_text(
            "<b>📨 Usage:</b> <code>/send user_id</code>\n"
            "<b>Or Reply To A Message With It.</b>"
        )
    target = int(arg)
    if await is_banned(target):
        return await message.reply_text(
            "<b>🚫 This User Is Banned.\n/unban First To Message Them.</b>"
        )

    if message.reply_to_message:
        msgs = await _messages_from_reply(bot, message.reply_to_message)
        return await _make_preview(bot, admin_id, message.chat.id, _to_units(msgs), target)

    state = _WAITING[admin_id] = {"t": time.time(), "msgs": [], "ack": None, "target": target}
    state["prompt_msg"] = await message.reply_text(
        f"<b>📨 Send Your Messages To</b> {await _user_label(bot, target)}<b>.</b>",
        reply_markup=_cancel_markup(),
    )


async def _ack(admin_id, chat_id, bot, delay):
    if delay:
        await asyncio.sleep(delay)
    state = _WAITING.get(admin_id)
    if not state:
        return
    units = _to_units(state["msgs"])
    old = state.get("ack_msg")
    state["ack_msg"] = await bot.send_message(
        chat_id,
        f"<b>✅ {len(units)} Message(s) Ready.\n\n"
        "Send More Or Tap Done.</b>",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Done", callback_data="bcw_done"),
            InlineKeyboardButton("❌ Cancel", callback_data="bcw_cancel"),
        ]]),
    )
    await _drop(old)


async def _drop(msg):
    """Delete a bot message (the old 'Ready' message), ignoring any error."""
    if msg:
        try:
            await msg.delete()
        except Exception:
            pass


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

    if first_word in ("/done", "/cancel"):
        raise StopPropagation   # these are buttons now: ignored, never sent as a message

    if len(state["msgs"]) >= MAX_MESSAGES:
        await message.reply_text(f"<b>Limit Reached ({MAX_MESSAGES} Messages). Tap Done Or Cancel.</b>")
        raise StopPropagation

    state["msgs"].append(message)
    _spawn(_strip_prompt(state))
    if state["ack"]:
        state["ack"].cancel()
    # a single message is acknowledged at once, an album after its last item arrived
    state["ack"] = _spawn(_ack(user.id, message.chat.id, bot, ALBUM_QUIET if message.media_group_id else 0))
    raise StopPropagation


@Client.on_callback_query(filters.regex(r"^bcw_(done|cancel)$") & admin_perm("can_broadcast"))
async def collect_buttons(bot, query):
    """Cancel (under the prompt and under the 'Ready' message) and Done (under 'Ready')."""
    user_id = query.from_user.id
    state = _WAITING.get(user_id)
    if not state or time.time() - state["t"] > IDLE_SECONDS:
        _reset_waiting(user_id)
        await query.answer("Nothing pending. Run /broadcast again.", show_alert=True)
        await _drop(query.message)
        return
    if query.data == "bcw_cancel":
        _WAITING.pop(user_id, None)
        if state["ack"]:
            state["ack"].cancel()
        await _close_collect(state, keep_id=query.message.id)
        await query.answer("Cancelled.")
        try:
            await query.message.edit_text("<b>Cancelled.</b>")
        except Exception:
            pass
        return
    if not state["msgs"]:
        return await query.answer("Nothing added yet. Send your message first.", show_alert=True)
    _WAITING.pop(user_id, None)
    if state["ack"]:
        state["ack"].cancel()
    await query.answer()
    chat_id = query.message.chat.id
    await _close_collect(state)
    await _drop(query.message)
    await _make_preview(bot, user_id, chat_id, _to_units(state["msgs"]), state.get("target"))


@Client.on_callback_query(filters.regex(r"^bc_(yes|no):[0-9a-f]{8}$") & admin_perm("can_broadcast"))
async def broadcast_answer(bot, query):
    action, _, token = query.data.partition(":")
    job = _JOBS.get(token)

    if not job:
        await query.answer("This preview expired. Run /broadcast again.", show_alert=True)
        try:
            await query.message.edit_text("<b>⌛ Preview Expired. Run /broadcast Again.</b>")
        except Exception:
            pass
        return
    if job["admin"] != query.from_user.id:
        return await query.answer("This broadcast belongs to another admin.", show_alert=True)

    if action == "bc_no":
        _JOBS.pop(token, None)
        await query.answer("Cancelled.")
        return await query.message.edit_text("<b>❌ Cancelled. Nothing Was Sent.</b>")

    if job.get("target"):
        _JOBS.pop(token, None)   # one confirm = one send
        await query.answer("Sending...")
        return await _run_direct(bot, query.message, job["target"], job["units"])

    if _lock().locked():
        return await query.answer("A broadcast is already running. Wait for it to finish.", show_alert=True)

    _JOBS.pop(token, None)   # one confirm = one broadcast (a second tap finds nothing)
    await query.answer("Broadcasting...")
    await _run_broadcast(bot, query.message, job["units"])


async def _run_direct(bot, sts, user_id, units):
    """Send to one user and tell the admin exactly what happened."""
    await sts.edit_text("<b>Sending...</b>")
    sent = 0
    reason = None
    try:
        for unit in units:
            await _deliver_with_retry(bot, user_id, unit)
            sent += 1
    except UserIsBlocked:
        reason = "That User Has Blocked The Bot."
    except InputUserDeactivated:
        reason = "That Account Is Deleted."
    except PeerIdInvalid:
        reason = "The Bot Can't Find This User. Check The ID. The User Must Have Started The Bot."
    except Exception as e:
        reason = f"Failed ({type(e).__name__})."

    if reason is None:
        text = f"<b>✅ Sent To User</b> <code>{user_id}</code><b>. ({sent} Message(s))</b>"
    else:
        text = (
            f"<b>❌ Not Sent To User</b> <code>{user_id}</code>\n<b>{reason}</b>\n"
            f"<b>Delivered Before The Error: {sent} Of {len(units)}</b>"
        )
    await sts.edit_text(text)
    log_text(bot, text)


async def _send_all(bot, targets, units, on_progress=None, every=20):
    """Send `units` to every target ({"user_id": ...}), CONCURRENCY users at a time, and return the counts.
    on_progress(counts) is awaited every `every` finished users."""
    counts = {"done": 0, "success": 0, "blocked": 0, "deleted": 0, "failed": 0}
    sem = asyncio.Semaphore(CONCURRENCY)

    async def _send(uid):
        async with sem:
            return await broadcast_messages(bot, uid, units)

    tasks = [asyncio.create_task(_send(t["user_id"])) for t in targets]
    for coro in asyncio.as_completed(tasks):
        ok, reason = await coro
        if ok:
            counts["success"] += 1
        elif reason == "Blocked":
            counts["blocked"] += 1
        elif reason == "Deleted":
            counts["deleted"] += 1
        else:
            counts["failed"] += 1
        counts["done"] += 1
        if on_progress and not counts["done"] % every:
            try:
                await on_progress(counts)
            except Exception:
                pass
    return counts


async def _run_broadcast(bot, sts, units):
    async with _lock():
        targets = await get_broadcast_targets()
        start_time = time.time()
        total_users = len(targets)
        await sts.edit_text("<b>📣 BROADCASTING…</b>")

        async def _progress(c):
            await sts.edit_text(_bot_text(c, total_users, False, time.time() - start_time))

        c = await _send_all(bot, targets, units, _progress)
        final_text = _bot_text(c, total_users, True, time.time() - start_time)
        await sts.edit_text(final_text)
        log_text(bot, final_text)
