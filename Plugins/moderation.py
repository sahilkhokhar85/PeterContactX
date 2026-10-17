"""Admin moderation commands: /ban /unban /banlist /status /info

/ban and /unban accept ids typed in the command (many at once), a .txt/.csv file of ids
(sent with the command as caption, or replied to), or a reply to a message the bot
forwarded to you from a user.
Banned ids (even ones that never used the bot) get no reply at all - see Plugins/ban_guard.py.
"""
import asyncio
import html
import io
import re
import time

from pyrogram import Client, filters
from pyrogram.types import Message

from config import OWNERS
from database import (
    admin_filter, admin_perm, ban_users, unban_users, get_banned_users, banned_users_count,
    total_users_count, get_admins, get_user, is_banned, is_admin,
)
from Plugins.contact import resolve_reply_target

try:
    import psutil
except ImportError:   # /status still works without it, CPU/RAM then show 0
    psutil = None

BOT_START_TIME = time.time()
_ID_RE = re.compile(r"\d{5,16}")
_MAX_ID_FILE = 5 * 1024 * 1024  # 5 MB is plenty for a list of ids


def get_readable_time(seconds: int) -> str:
    periods = [("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1)]
    result = []
    for name, secs in periods:
        val, seconds = divmod(seconds, secs)
        if val:
            result.append(f"{val} {name}{'s' if val != 1 else ''}")
    return " ".join(result) if result else "0 seconds"


def ids_from_text(text: str) -> set:
    """Ids typed in the command: every 5-16 digit number counts."""
    return {int(x) for x in _ID_RE.findall(text or "")}


def ids_from_file_text(text: str) -> set:
    """Ids from a .txt / .csv file, line by line.
    - a line of only numbers (one or many, any separator) -> all of them are ids
    - a CSV row like "123456789,Name,username" -> only the FIRST column is the id
      (digits inside names / usernames are never banned by mistake)
    - header lines such as "id,firstname,..." are ignored"""
    ids = set()
    for line in (text or "").splitlines():
        line = line.strip().lstrip("﻿")
        if not line:
            continue
        if re.fullmatch(r"[\d\s,;\t]+", line):
            ids |= {int(x) for x in _ID_RE.findall(line)}
            continue
        first = re.split(r"[,;\t ]", line, maxsplit=1)[0].strip()
        if re.fullmatch(r"\d{5,16}", first):
            ids.add(int(first))
    return ids


async def _collect_target_ids(client, message: Message) -> set:
    ids = set()
    text = message.text or message.caption or ""
    parts = text.split(None, 1)
    if len(parts) > 1:
        ids |= ids_from_text(parts[1])

    for doc_msg in (message, message.reply_to_message):
        doc = getattr(doc_msg, "document", None) if doc_msg else None
        if doc and (doc.file_size or 0) <= _MAX_ID_FILE:
            try:
                data = await client.download_media(doc, in_memory=True)
                ids |= ids_from_file_text(bytes(data.getbuffer()).decode("utf-8", errors="ignore"))
            except Exception as e:
                await message.reply_text(f"<b>❌ Couldn't Read That File:</b> <code>{type(e).__name__}</code>")

    if not ids and message.reply_to_message:
        user_id = await resolve_reply_target(message.chat.id, message.reply_to_message)
        if user_id:
            ids.add(user_id)
    return ids


@Client.on_message(filters.command("ban") & filters.private & admin_perm("can_settings"))
async def ban_cmd(client: Client, message: Message):
    ids = await _collect_target_ids(client, message)
    if not ids:
        return await message.reply_text(
            "<b>Usage:</b>\n"
            "<code>/ban user_id</code>\n"
            "<code>/ban id1 id2 id3 ...</code> <b>(Many Ids At Once)</b>\n"
            "<b>Send Or Reply With A .txt File Of Ids Using /ban\n"
            "Or Reply To A Message The Bot Forwarded To You With /ban\n\n"
            "Ids Don't Need To Have Used The Bot. They Are Blocked Forever And Get No Reply.</b>"
        )
    protected = set(OWNERS) | set(await get_admins())
    skipped = ids & protected
    ids -= protected
    new = await ban_users(ids) if ids else 0
    text = (
        f"<b>✅ Banned {new} New Id(s).</b>\n"
        f"<b>Already Banned:</b> <code>{len(ids) - new}</code>\n"
    )
    if skipped:
        text += f"<b>Skipped Admins:</b> <code>{len(skipped)}</code>\n"
    text += f"<b>Total Banned Now:</b> <code>{await banned_users_count()}</code>"
    await message.reply_text(text)


@Client.on_message(filters.command("unban") & filters.private & admin_perm("can_settings"))
async def unban_cmd(client: Client, message: Message):
    ids = await _collect_target_ids(client, message)
    if not ids:
        return await message.reply_text(
            "<b>Usage:</b>\n"
            "<code>/unban user_id</code>\n"
            "<code>/unban id1 id2 id3 ...</code>\n"
            "<b>Or Reply To A Forwarded Message / A .txt File Of Ids With /unban</b>"
        )
    removed = await unban_users(ids)
    await message.reply_text(
        f"<b>✅ Unbanned {removed} Id(s).</b>\n"
        f"<b>Not In The Ban List:</b> <code>{len(ids) - removed}</code>\n"
        f"<b>Total Banned Now:</b> <code>{await banned_users_count()}</code>"
    )


@Client.on_message(filters.command("banlist") & filters.private & admin_perm("can_settings"))
async def banlist_cmd(client: Client, message: Message):
    """Sends the whole ban list as a .txt file (handy as a backup, and /ban accepts the same file back)."""
    ids = await get_banned_users()
    if not ids:
        return await message.reply_text("<b>The Ban List Is Empty.</b>")
    buf = io.BytesIO("\n".join(str(i) for i in ids).encode("utf-8"))
    buf.name = "banned_ids.txt"
    await message.reply_document(buf, caption=f"<b>🚫 Banned Ids: {len(ids)}</b>")


@Client.on_message(filters.command("status") & filters.private & admin_filter)
async def bot_status(client: Client, message: Message):
    total_users = await total_users_count()
    total_banned = await banned_users_count()
    try:
        cpu = await asyncio.to_thread(psutil.cpu_percent, 0.5)
        ram = psutil.virtual_memory().percent
    except Exception:
        cpu = ram = 0
    uptime = get_readable_time(int(time.time() - BOT_START_TIME))
    text = (
        "<b>🤖 BOT STATUS !!</b>\n\n"
        f"<b>👤 Users -</b> <code>{total_users}</code>\n"
        f"<b>🚫 Ban Users -</b> <code>{total_banned}</code>\n"
        f"<b>⚙️ CPU -</b> <code>{cpu}%</code>\n"
        f"<b>💾 RAM -</b> <code>{ram}%</code>\n"
        f"<b>⚡ Uptime -</b> <code>{uptime}</code>"
    )
    await message.reply_text(text)


@Client.on_message(filters.command("info") & filters.private & admin_perm("can_settings"))
async def info_cmd(client: Client, message: Message):
    """/info (reply to a forwarded message) or /info <user_id>: who is this user?"""
    ids = await _collect_target_ids(client, message)
    if not ids:
        return await message.reply_text(
            "<b>Usage:</b> <b>Reply To A Forwarded Message With /info, Or Send</b> <code>/info user_id</code>"
        )
    for user_id in list(ids)[:5]:
        doc = await get_user(user_id)
        lines = [f"<b>👤 User</b> <code>{user_id}</code>"]
        if doc:
            name = html.escape(doc.get("first_name") or "-")
            username = f"@{html.escape(doc['username'])}" if doc.get("username") else "-"
            lines.append(f"<b>Name: {name}\nUsername: {username}</b>")
            joined = doc.get("joined_at")
            if joined:
                lines.append(f"<b>First Seen:</b> <code>{joined:%Y-%m-%d}</code>")
        else:
            lines.append("<b>Not In The User List (Never Used The Bot, Or Was Removed After Blocking It).</b>")
        lines.append(f"<b>Banned:</b> <code>{'yes' if await is_banned(user_id) else 'no'}</code>")
        if await is_admin(user_id):
            lines.append("<b>Role:</b> <code>admin</code>")
        lines.append(f'<a href="tg://user?id={user_id}"><b>Open Profile</b></a>')
        await message.reply_text("\n".join(lines))
