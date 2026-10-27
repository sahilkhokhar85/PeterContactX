"""Two guards that run before every other plugin.

1) Group -6: the bot's OWN messages (and edits) are never handled. Without this the bot answered its own
   replies as if a user had written them: it forwarded its own start message to the admins and
   answered itself with "Message Sent!", over and over.
2) Group -5: silent block for banned users.

Runs in group -5 (before every other plugin). If the sender is banned the update
is dropped here: no reply, no forward to the admins, no "you are banned" message.
Owners and bot admins are never blocked. The ban list lives in memory (loaded
once from Mongo), so this adds no database call per message.
"""
import logging

from pyrogram import Client, filters, StopPropagation

from database import is_banned, is_admin
from logchannel import log_event, once_per

logger = logging.getLogger(__name__)


async def _is_own(_, __, message) -> bool:
    """True for a message the bot itself sent (or edited) in a private chat."""
    user = getattr(message, "from_user", None)
    return bool(getattr(message, "outgoing", False) or (user is not None and getattr(user, "is_self", False)))


from_self = filters.create(_is_own)


@Client.on_message(filters.private & from_self, group=-6)
async def ignore_own_messages(client, message):
    raise StopPropagation


@Client.on_edited_message(filters.private & from_self, group=-6)
async def ignore_own_edits(client, message):
    raise StopPropagation


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
        if once_per(("banned", message.from_user.id), 600):   # a banned spammer can't flood the log channel
            log_event(client, "🚫 Banned User Message (Ignored)", user=message.from_user, message=message)
        raise StopPropagation


@Client.on_callback_query(group=-5)
async def block_banned_callbacks(client, query):
    if await _blocked(query.from_user):
        if once_per(("banned", query.from_user.id), 600):
            log_event(client, "🚫 Banned User Button Press (Ignored)", user=query.from_user)
        raise StopPropagation
