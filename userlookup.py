"""Name lookup through an optional user account (SESSION_STRING).

A bot can only find users it has talked to. A normal account knows everybody it has chats / groups with,
so the banned list asks this account for the names the bot does not know. Read-only: it never sends anything.
"""
import asyncio
import logging

from pyrogram import Client

from config import API_ID, API_HASH, SESSION_STRING

logger = logging.getLogger(__name__)

_client = None
_warm = None


def enabled() -> bool:
    return _client is not None


async def _warm_up():
    """Lets the account load its chats, so users from them can be found by id."""
    try:
        async for _ in _client.get_dialogs(limit=1000):
            pass
        logger.info("lookup account: chats loaded")
    except Exception as e:
        logger.warning(f"lookup account: couldn't load chats ({type(e).__name__}: {e})")


async def start():
    global _client, _warm
    if not SESSION_STRING or _client is not None:
        return
    try:
        _client = Client("lookup_account", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING,
                         in_memory=True, no_updates=True)
        await _client.start()
        me = await _client.get_me()
        print(f"Lookup account ready: {me.first_name}")
        _warm = asyncio.create_task(_warm_up())
    except Exception as e:
        print(f"Lookup account error ({type(e).__name__}): {e}. Names are looked up by the bot only.")
        _client = None


async def stop():
    global _client
    if _warm:
        _warm.cancel()
    if _client is not None:
        try:
            await _client.stop()
        except Exception:
            pass
        _client = None


async def lookup_name(user_id: int):
    """The name of a user as that account sees it, or None."""
    if _client is None:
        return None
    try:
        u = await asyncio.wait_for(_client.get_users(user_id), 6)
    except Exception:
        return None
    name = " ".join(x for x in (getattr(u, "first_name", None), getattr(u, "last_name", None)) if x)
    return name or None
