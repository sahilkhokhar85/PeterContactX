import os
import sys
import html
import asyncio
import logging

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database import admin_filter, add_user, is_banned, is_user_exist
from logchannel import log_user_message, log_now
from Plugins.forcesub import is_subscribed, send_force_sub_prompt
from texts import START_IMAGE, START_TEXT

logger = logging.getLogger(__name__)


async def send_start_message(client: Client, chat_id: int, user):
    """The permanent /start message (see texts.py): image + bold text. Falls back to text only if the image can't be sent."""
    mention = f'<a href="tg://user?id={user.id}">{html.escape(user.first_name or "there")}</a>'
    text = START_TEXT.replace("{mention}", mention)
    try:
        await client.send_photo(chat_id, START_IMAGE, caption=text, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"start image failed ({type(e).__name__}: {e}) - sending text only")
        await client.send_message(chat_id, text, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    user_id = message.from_user.id

    if await is_banned(user_id):
        return

    if not await is_subscribed(client, user_id):
        return await send_force_sub_prompt(client, message)

    is_new = not await is_user_exist(user_id)
    await add_user(user_id, message.from_user.first_name or "", message.from_user.username or "")
    if is_new:
        log_user_message(client, message, "🆕 New User")
    await send_start_message(client, message.chat.id, message.from_user)


@Client.on_message(filters.command("restart") & filters.private & admin_filter)
async def restart_bot(client: Client, message: Message):
    await message.reply_text("<b>🔄 Restarting...</b>", parse_mode=enums.ParseMode.HTML)
    await log_now(client, f"<b>🔄 Bot Restarting</b>\n\n<b>👮 By:</b> <code>{message.from_user.id}</code>")
    await asyncio.sleep(2)
    os.execl(sys.executable, sys.executable, *sys.argv)
