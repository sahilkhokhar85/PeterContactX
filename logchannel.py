"""Log channel: a private record of everything, so you can see WHO wrote even when the user hides their profile.

Set LOG_CHANNEL (channel id, e.g. -1001234567890) and make the bot an admin of that channel.
Every entry is a short bold header with name / id / username, then a copy of the message under it.
Nothing is ever sent to the bot chat because of this, and a failing log never affects the bot.
"""
import asyncio
import html
import logging

from pyrogram import enums

from config import LOG_CHANNEL
from database import get_user

logger = logging.getLogger(__name__)
HTML = enums.ParseMode.HTML

_tasks = set()
_last_album = {}   # user_id -> media_group_id already logged (an album gets ONE header)
_warned = False


def enabled() -> bool:
    return bool(LOG_CHANNEL)


def _fire(coro):
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _who(user_id: int, name: str = "", username: str = "") -> str:
    name = html.escape(name or "User")
    lines = [
        f'<b>👤 Name:</b> <a href="tg://user?id={user_id}">{name}</a>',
        f"<b>🆔 ID:</b> <code>{user_id}</code>",
    ]
    if username:
        lines.append(f"<b>🔗 Username:</b> @{html.escape(username)}")
    return "\n".join(lines)


async def _user_block(user_id: int) -> str:
    doc = await get_user(user_id) or {}
    return _who(user_id, doc.get("first_name", ""), doc.get("username", ""))


async def _post(client, text: str, message=None):
    global _warned
    try:
        header = await client.send_message(LOG_CHANNEL, text, parse_mode=HTML)
        if message is not None:
            await message.copy(LOG_CHANNEL, reply_to_message_id=header.id)
    except Exception as e:
        if not _warned:   # one warning is enough, no log spam
            _warned = True
            logger.warning(f"[LOG] can't write to LOG_CHANNEL ({type(e).__name__}: {e}). Is the bot an admin there?")


# ---------------- what gets logged ----------------

async def log_now(client, text: str):
    """Same as log_text but waits until it is sent (used right before a restart)."""
    if enabled():
        await _post(client, text)


def log_text(client, text: str):
    """A plain bold/HTML text entry (bans, broadcast result...)."""
    if enabled():
        _fire(_post(client, text))


def log_user_message(client, message, title: str = "📩 New Message"):
    """A message (or edit) written by a user."""
    if not enabled() or not message.from_user:
        return
    user = message.from_user
    if message.media_group_id:
        if _last_album.get(user.id) == message.media_group_id:
            _fire(message.copy(LOG_CHANNEL))   # same album: no second header
            return
        _last_album[user.id] = message.media_group_id
        if len(_last_album) > 5000:
            _last_album.clear()
    text = f"<b>{title}</b>\n\n" + _who(user.id, user.first_name or "", user.username or "")
    _fire(_post(client, text, message))


def log_admin_reply(client, message, user_id: int, title: str = "↩️ Reply Sent"):
    """A reply (or edited reply) an admin sent to a user."""
    if not enabled():
        return

    async def _go():
        text = f"<b>{title}</b>\n\n" + await _user_block(user_id) + f"\n<b>👮 By:</b> <code>{message.chat.id}</code>"
        await _post(client, text, message)

    _fire(_go())
