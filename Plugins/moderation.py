"""Admin moderation commands: /ban /unban /banlist /status /info

/ban and /unban accept ids typed in the command (many at once), a .txt/.csv file of ids
(sent with the command as caption, or replied to), or a reply to a message the bot
forwarded to you from a user.
Banned ids (even ones that never used the bot) get no reply at all - see Plugins/ban_guard.py.
"""
import asyncio
import io
import re
import time

from pyrogram import Client, filters
from pyrogram.types import Message

from config import OWNERS
from database import (
    admin_perm, ban_users, unban_users, get_banned_users, banned_users_count,
    total_users_count, admins_count, get_admins, get_user, is_banned, is_admin,
    content_count,
)
from Plugins.contact import resolve_reply_target
from Plugins.broadcast import is_running

try:
    import psutil
except ImportError:   # /status still works without it, just without CPU/RAM
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
                await message.reply_text(f"**❌ Couldn't read that file:** `{type(e).__name__}`")

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
            "**Usage:**\n"
            "`/ban user_id`\n"
            "`/ban id1 id2 id3 ...` (many ids at once)\n"
            "Send or reply with a .txt file of ids using /ban\n"
            "Or reply to a message the bot forwarded to you with /ban\n\n"
            "_Banned ids are blocked and get no reply._"
        )
    protected = set(OWNERS) | set(await get_admins())
    skipped = ids & protected
    ids -= protected
    new = await ban_users(ids) if ids else 0
    text = f"**✅ Banned {new} new id(s).**\nAlready banned: `{len(ids) - new}`\n"
    if skipped:
        text += f"Skipped admins: `{len(skipped)}`\n"
    text += f"Total banned now: `{await banned_users_count()}`"
    await message.reply_text(text)


@Client.on_message(filters.command("unban") & filters.private & admin_perm("can_settings"))
async def unban_cmd(client: Client, message: Message):
    ids = await _collect_target_ids(client, message)
    if not ids:
        return await message.reply_text(
            "**Usage:**\n"
            "`/unban user_id`\n"
            "`/unban id1 id2 id3 ...`\n"
            "Or reply to a forwarded message / a .txt file of ids with /unban"
        )
    removed = await unban_users(ids)
    await message.reply_text(
        f"**✅ Unbanned {removed} id(s).**\n"
        f"Not in the ban list: `{len(ids) - removed}`\n"
        f"Total banned now: `{await banned_users_count()}`"
    )


@Client.on_message(filters.command("banlist") & filters.private & admin_perm("can_settings"))
async def banlist_cmd(client: Client, message: Message):
    """Sends the whole ban list as a .txt file (handy as a backup, and /ban accepts the same file back)."""
    ids = await get_banned_users()
    if not ids:
        return await message.reply_text("**The ban list is empty.**")
    buf = io.BytesIO("\n".join(str(i) for i in ids).encode("utf-8"))
    buf.name = "banned_ids.txt"
    await message.reply_document(buf, caption=f"**🚫 Banned ids: {len(ids)}**")


@Client.on_message(filters.command("status") & filters.private & admin_perm("can_settings"))
async def status_cmd(client: Client, message: Message):
    cpu = ram = None
    if psutil:
        try:
            cpu = await asyncio.to_thread(psutil.cpu_percent, 0.5)
            ram = psutil.virtual_memory().percent
        except Exception:
            pass
    text = (
        "**🤖 BOT STATUS**\n\n"
        f"👤 Users - `{await total_users_count()}`\n"
        f"🚫 Banned - `{await banned_users_count()}`\n"
        f"👮 Admins - `{await admins_count()}`\n"
        f"📚 Indexed posts - `{await content_count()}`\n"
        f"📢 Broadcast running - `{'yes' if is_running() else 'no'}`\n"
    )
    if cpu is not None:
        text += f"⚙️ CPU - `{cpu}%`\n💾 RAM - `{ram}%`\n"
    text += f"⚡ Uptime - `{get_readable_time(int(time.time() - BOT_START_TIME))}`"
    await message.reply_text(text)


@Client.on_message(filters.command("info") & filters.private & admin_perm("can_settings"))
async def info_cmd(client: Client, message: Message):
    """/info (reply to a forwarded message) or /info <user_id>: who is this user?"""
    ids = await _collect_target_ids(client, message)
    if not ids:
        return await message.reply_text("**Usage:** reply to a forwarded message with /info, or send `/info user_id`")
    for user_id in list(ids)[:5]:
        doc = await get_user(user_id)
        lines = [f"**👤 User** `{user_id}`"]
        if doc:
            name = doc.get("first_name") or "-"
            username = f"@{doc['username']}" if doc.get("username") else "-"
            lines.append(f"Name: {name}\nUsername: {username}")
            joined = doc.get("joined_at")
            if joined:
                lines.append(f"First seen: `{joined:%Y-%m-%d}`")
        else:
            lines.append("_Not in the user list (never used the bot, or was removed after blocking it)._")
        lines.append(f"Banned: `{'yes' if await is_banned(user_id) else 'no'}`")
        if await is_admin(user_id):
            lines.append("Role: `admin`")
        lines.append(f"[Open profile](tg://user?id={user_id})")
        await message.reply_text("\n".join(lines))
