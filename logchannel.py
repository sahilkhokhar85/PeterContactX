"""Log channel: a private record of EVERYTHING that happens in the bot.

Set LOG_CHANNEL (channel id, e.g. -1001234567890) and make the bot an admin of that channel.
Every entry is a short bold header (title, name / id / username of the user, the admin who did
it, a few detail lines) and, where there is one, a copy of the message right under it.

* Nothing is ever sent to the bot chat because of this.
* Entries are written one after another by a single worker (so the order is the order things
  happened), FloodWait is waited out, and a failing log never affects the bot.
"""
import asyncio
import html
import logging
import time

from pyrogram import enums, types
from pyrogram.errors import FloodWait

from config import LOG_CHANNEL
from database import get_user

logger = logging.getLogger(__name__)
HTML = enums.ParseMode.HTML

_queue = None
_worker = None
_warned = False
_last_album = {}    # user_id -> media_group_id already logged (an album gets ONE header)
_seen = {}          # throttle keys -> last time


def enabled() -> bool:
    return bool(LOG_CHANNEL)


def once_per(key, seconds: float) -> bool:
    """True the first time per `seconds` for this key (used so a spammer can't flood the log channel)."""
    now = time.time()
    if now - _seen.get(key, 0) < seconds:
        return False
    _seen[key] = now
    if len(_seen) > 20000:
        for k in [k for k, t in _seen.items() if now - t > 3600]:
            _seen.pop(k, None)
    return True


def clip(text, limit: int = 300) -> str:
    """Short, HTML-safe excerpt of some text."""
    text = " ".join(str(text or "").split())
    return html.escape(text if len(text) <= limit else text[:limit] + "…")


async def with_reply(fn, *args, reply_id, **kwargs):
    """Calls fn(*args, **kwargs) as a reply to message `reply_id`: with the new `reply_parameters` where the
    library has it, else with the old `reply_to_message_id` (no deprecation warning, works with both)."""
    params = getattr(types, "ReplyParameters", None)
    if params is not None:
        try:
            return await fn(*args, reply_parameters=params(message_id=reply_id), **kwargs)
        except TypeError:
            pass   # this version doesn't know it yet
    return await fn(*args, reply_to_message_id=reply_id, **kwargs)


# ---------------- the single ordered worker ----------------

async def _retry(fn):
    for _ in range(6):
        try:
            return await fn()
        except FloodWait as e:
            await asyncio.sleep(int(e.value) + 1)
    return await fn()


async def _run():
    global _warned
    while True:
        job = await _queue.get()
        try:
            await job()
        except Exception as e:
            if not _warned:   # one warning is enough, no log spam
                _warned = True
                logger.warning(f"[LOG] can't write to LOG_CHANNEL ({type(e).__name__}: {e}). Is the bot an admin there?")
        finally:
            _queue.task_done()


def _enqueue(job):
    global _queue, _worker
    if _queue is None:
        _queue = asyncio.Queue()
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_run())
    _queue.put_nowait(job)


# ---------------- text building ----------------

def _name_link(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name or "User")}</a>'


def _who(user_id: int, name: str = "", username: str = "") -> str:
    lines = [
        f"<b>👤 Name:</b> {_name_link(user_id, name)}",
        f"<b>🆔 ID:</b> <code>{user_id}</code>",
    ]
    if username:
        lines.append(f"<b>🔗 Username:</b> @{html.escape(username)}")
    return "\n".join(lines)


async def _user_block(user=None, uid=None) -> str:
    if user is not None:
        return _who(user.id, user.first_name or "", getattr(user, "username", "") or "")
    doc = await get_user(uid) or {}
    return _who(uid, doc.get("first_name", ""), doc.get("username", ""))


def _by_line(by) -> str:
    if by is None:
        return ""
    if isinstance(by, int):
        return f"<b>👮 By:</b> <code>{by}</code>"
    name = " ".join(x for x in (getattr(by, "first_name", ""), getattr(by, "last_name", "")) if x)
    return f"<b>👮 By:</b> {_name_link(by.id, name)} (<code>{by.id}</code>)"


async def _post(client, text: str, message=None, units=None):
    header = await _retry(lambda: client.send_message(LOG_CHANNEL, text, parse_mode=HTML))
    if message is not None:
        await _retry(lambda: with_reply(message.copy, LOG_CHANNEL, reply_id=header.id))
    for unit in units or []:
        for m in unit:
            await _retry(lambda m=m: with_reply(m.copy, LOG_CHANNEL, reply_id=header.id))


# ---------------- what gets logged ----------------

def log_event(client, title: str, *, user=None, uid=None, by=None, lines=(), message=None, units=None):
    """One log entry.
    user / uid : the person the event is about (a pyrogram User, or just an id looked up in the database)
    by         : the admin who did it (User or id)
    lines      : extra ready-made HTML detail lines
    message    : a message that is copied under the entry
    units      : broadcast units (lists of messages) that are copied under the entry"""
    if not enabled():
        return

    async def _job():
        parts = [f"<b>{title}</b>"]
        body = []
        if user is not None or uid is not None:
            body.append(await _user_block(user, uid))
        if by is not None:
            body.append(_by_line(by))
        if lines:
            body.append("\n".join(lines))
        text = parts[0] + ("\n\n" + "\n\n".join(b for b in body if b) if body else "")
        await _post(client, text, message, units)

    _enqueue(_job)


def log_text(client, text: str):
    """A ready-made HTML entry."""
    if enabled():
        async def _job():
            await _post(client, text)
        _enqueue(_job)


async def log_now(client, text: str):
    """Writes an entry and waits until everything before it is written too (used right before a restart / stop)."""
    if not enabled():
        return
    log_text(client, text)
    try:
        await asyncio.wait_for(_queue.join(), 15)
    except Exception:
        pass


def log_user_message(client, message, title: str = "📩 New Message"):
    """A message (or edit) written by a user."""
    if not enabled() or not message.from_user:
        return
    user = message.from_user
    if message.media_group_id:
        if _last_album.get(user.id) == message.media_group_id:
            async def _more():   # same album: no second header
                await _retry(lambda: message.copy(LOG_CHANNEL))
            _enqueue(_more)
            return
        _last_album[user.id] = message.media_group_id
        if len(_last_album) > 5000:
            _last_album.clear()
    log_event(client, title, user=user, message=message)


def log_admin_reply(client, message, user_id: int, title: str = "↩️ Reply Sent"):
    """A reply (or edited reply) an admin sent to a user."""
    log_event(client, title, uid=user_id, by=message.from_user or message.chat.id, message=message)
