"""Silent block for banned users.

Runs in group -5 (before every other plugin). If the sender is banned the update
is dropped here: no reply, no forward to the admins, no "you are banned" message.
Owners and bot admins are never blocked. The ban list lives in memory (loaded
once from Mongo), so this adds no database call per message.
"""
import logging

from pyrogram import Client, filters, StopPropagation

from database import is_banned, is_admin

logger = logging.getLogger(__name__)


async def _blocked(user) -> bool:
    if not user:
        return False
    try:
        if not await is_banned(user.id):      # in-memory, covers 99.9% of updates
            return False
        return not await is_admin(user.id)    # a banned id that is also an admin is never blocked
    except Exception as e:
        # If the database can't be read, don't lock everybody out.
        logger.warning(f"ban check failed, letting the update through: {type(e).__name__}: {e}")
        return False


@Client.on_message(filters.incoming, group=-5)
async def block_banned_messages(client, message):
    if await _blocked(message.from_user):
        raise StopPropagation


@Client.on_callback_query(group=-5)
async def block_banned_callbacks(client, query):
    if await _blocked(query.from_user):
        raise StopPropagation
